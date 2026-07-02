import logging
import time

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from apps.resources.models import Resource
from apps.resources.serializers import ResourceListSerializer

from utils import rag
from utils.llm import llm_client
from utils.sse import _format_sse, build_references_events
from apps.classify.utils.query_parser import parse as parse_query

from .models import Conversation, ChatMessage, QueryLog
from .serializers import (
    ConversationSerializer,
    ConversationListSerializer,
    ChatMessageSerializer,
    AskSerializer,
    FeedbackSerializer,
    MessageRateSerializer,
)


logger = logging.getLogger(__name__)


@extend_schema(
    summary='会话管理',
    description='会话管理接口，支持列表、创建、详情、编辑、删除',
    tags=['AI对话'],
)
class ConversationViewSet(ModelViewSet):
    """
    会话 ViewSet

    GET    /api/chat/conversations/           - 会话列表
    POST   /api/chat/conversations/          - 新建会话（可选）
    GET    /api/chat/conversations/{id}/     - 会话详情
    PATCH  /api/chat/conversations/{id}/     - 编辑会话
    DELETE /api/chat/conversations/{id}/     - 删除会话
    """
    serializer_class = ConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).prefetch_related('messages')

    def get_serializer_class(self):
        if self.action == 'list':
            return ConversationListSerializer
        return ConversationSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(
    summary='消息管理',
    description='消息反馈和引用来源查看',
    tags=['AI对话'],
)
class MessageViewSet(ModelViewSet):
    """
    消息 ViewSet（用于消息反馈和引用来源查看）

    POST   /api/chat/message/{id}/rate/        - 消息反馈（点赞/点踩）
    GET    /api/chat/message/{id}/references/   - 查看引用来源
    """
    queryset = ChatMessage.objects.all()
    serializer_class = ChatMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='rate')
    def rate(self, request, pk=None):
        """
        POST /api/chat/message/{id}/rate/
        消息反馈：点赞(like)或点踩(dislike)
        """
        serializer = MessageRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = self.get_object()
        rating = serializer.validated_data['rating']

        # 权限校验：只能对自己的消息进行反馈，或者当前用户是消息所属用户
        if message.conversation.user_id != request.user.id:
            return Response(
                {'detail': '无权对此消息进行反馈'},
                status=status.HTTP_403_FORBIDDEN
            )

        # 更新消息反馈（存入 ChatMessage 或关联的 QueryLog）
        # 这里先更新 ChatMessage 的 rating 字段（需要先添加到模型）
        # 同时更新 QueryLog 的 user_feedback
        message.rating = rating
        message.save(update_fields=['rating'])

        # 更新关联的 QueryLog
        QueryLog.objects.filter(
            conversation=message.conversation,
            question=message.content
        ).update(user_feedback=1 if rating == 'like' else 0)

        return Response({'status': 'ok', 'rating': rating})

    @action(detail=True, methods=['get'], url_path='references')
    def references(self, request, pk=None):
        """
        GET /api/chat/message/{id}/references/
        查看引用来源文档（仅 admin/super_admin 可用）
        """
        from config.permissions import CanViewReferences

        # 权限检查
        if request.user.role not in ('admin', 'super_admin'):
            return Response(
                {'detail': '仅管理员可查看引用来源'},
                status=status.HTTP_403_FORBIDDEN
            )

        message = self.get_object()
        resource_ids = message.references or []

        if not resource_ids:
            return Response([])

        # 根据用户角色过滤资源
        queryset = Resource.objects.filter(id__in=resource_ids)
        if request.user.role == 'admin':
            # admin 只能看自己上传的资源引用
            queryset = queryset.filter(uploader=request.user)

        serializer = ResourceListSerializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema(
    summary='会话列表',
    description='返回当前用户的会话列表',
    tags=['AI对话'],
    operation_id='会话列表',
)
class ConversationListView(generics.ListAPIView):
    """
    会话列表（兼容旧接口）

    GET /api/chat/conversations/
    功能：返回当前用户的会话列表（不含消息详情），按更新时间倒序
    """
    serializer_class = ConversationListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).order_by('-updated_at')


@extend_schema(
    summary='会话详情',
    description='返回指定会话的完整消息列表',
    tags=['AI对话'],
    operation_id='会话详情',
)
class ConversationDetailView(generics.RetrieveAPIView):
    """
    会话详情（兼容旧接口）

    GET /api/chat/conversations/<id>/
    功能：返回指定会话的完整消息列表（包含 AI 回答和用户提问）
    """
    serializer_class = ConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).prefetch_related('messages')


@extend_schema(
    summary='AI问答',
    description='AI问答接口，流式输出回答结果',
    tags=['AI对话'],
    operation_id='AI问答',
)
class AskView(APIView):
    """
    AI 问答接口（流式输出）

    POST /api/chat/ask/
    功能：
        1. 接收用户提问 → RAG 双路检索 → AI 流式生成
        2. 支持多轮对话（传入 conversation_id）
        3. SSE 逐字输出（text/event-stream）
        4. 流结束后自动生成 3 个相关推荐问题
        5. 按用户角色返回引用溯源
    参数：
        - conversation_id (int, optional): 会话ID（不传则新建会话）
        - question (str, required): 用户提问（最长 2000 字符）
    返回：SSE 流式响应（text/event-stream）
        事件类型：
        - event: start          → data: {"conversation_id": int, "message_id": int}
        - event: token          → data: {"content": "..."}
        - event: suggested      → data: ["问题1", "问题2", "问题3"]
        - event: references     → data: {"count": 3, "summary": "参考了3份文档"}
        - event: references_detail → data: [{"doc_id": int, "doc_title": "...", ...}]
        - event: done           → data: {"message_id": int}
    权限：
        - 所有登录用户可用
        - 引用详情：admin/super_admin 可看完整详情，普通用户仅看摘要
    注意：
        - 前端用 EventSource 或 fetch + ReadableStream 消费
        - 捕获 error 事件处理异常
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        question = serializer.validated_data['question']
        conversation_id = serializer.validated_data.get('conversation_id')
        user = request.user
        user_context = {
            'user_id': user.id,
            'role': getattr(user, 'role', 'user'),
            'college_id': getattr(user, 'college_id', None),
        }

        # 1. 获取或新建会话
        if conversation_id:
            conversation = get_object_or_404(
                Conversation, id=conversation_id, user=user,
            )
        else:
            conversation = Conversation.objects.create(
                user=user, title=question[:50],
            )
            logger.info('新建会话 id=%d user=%s', conversation.id, user.username)

        # 2. 保存用户消息
        ChatMessage.objects.create(
            conversation=conversation, role='user', content=question,
        )

        # 3. 获取历史消息（用于多轮对话上下文）
        history = list(
            conversation.messages
            .filter(role__in=['user', 'assistant'])
            .order_by('created_at')
            .values('role', 'content')
        )

        # 3.1 问题解析（LLM 抽取硬过滤条件，失败走规则回退）
        parsed = parse_query(question, user_context)
        logger.debug(f'parsed_filters: {parsed}')

        # 3.2 RAG 检索（新版：硬过滤 + 双路召回 + 权限校验）
        results = rag.retrieve(
            question=question,
            user_context=user_context,
            parsed_filters=parsed,
        )

        # 3.3 写 query_log（预创建，response_ms 在流结束后回填）
        start_ts = time.time()
        query_log = QueryLog.objects.create(
            user=user,
            conversation=conversation,
            question=question,
            parsed_filters=parsed,
            hit_count=len(results),
            top_doc_ids=[r['doc_id'] for r in results],
            response_ms=0,
        )

        # 5. 构造 Prompt（含历史上下文）
        messages = rag.build_rag_prompt(question, results, history=history)

        # 6. 创建 AI 消息占位（流结束后写入完整内容）
        is_authorized = user.role in ('admin', 'super_admin')
        references_detail = build_references_events(
            results, user.role, user.id,
        ).get('references_detail', [])

        assistant_msg = ChatMessage.objects.create(
            conversation=conversation, role='assistant',
            content='', references=references_detail,
        )

        # 7. SSE 流式响应
        def event_stream():
            """生成 SSE 事件流（finally 保证 query_log 回填）"""
            try:
                # start 事件
                yield _format_sse('start', {
                    'conversation_id': conversation.id,
                    'message_id': assistant_msg.id,
                })

                # 流式生成
                collected_tokens = []
                stream = llm_client.chat_stream(messages)
                for token in stream:
                    if token:
                        collected_tokens.append(token)
                        yield _format_sse('token', {'content': token})

                full_answer = ''.join(collected_tokens)

                # 保存 AI 回答（流结束后写库）
                if full_answer:
                    ChatMessage.objects.filter(id=assistant_msg.id).update(
                        content=full_answer,
                    )
                    logger.info(
                        'AI 回答完成 msg_id=%d conv_id=%d tokens=%d',
                        assistant_msg.id, conversation.id, len(collected_tokens),
                    )

                # 生成相关推荐（调用 LLM 生成 3 个相关问题）
                suggested = rag.generate_suggested_questions(question, full_answer)
                if suggested:
                    yield _format_sse('suggested', suggested)

                # 引用溯源
                refs = build_references_events(results, user.role, user.id)
                if 'references' in refs:
                    yield _format_sse('references', refs['references'])
                if 'references_detail' in refs and is_authorized:
                    yield _format_sse('references_detail', refs['references_detail'])

                # done 事件
                yield _format_sse('done', {'message_id': assistant_msg.id})

            finally:
                # 保证流中断时 response_ms 也能回填（客户端断连、Django 回收 generator 时触发）
                elapsed_ms = int((time.time() - start_ts) * 1000)
                try:
                    QueryLog.objects.filter(id=query_log.id).update(response_ms=elapsed_ms)
                except Exception:
                    logger.warning('回填 response_ms 失败 query_log.id=%d', query_log.id)

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        response['Access-Control-Allow-Origin'] = '*'
        return response


class FeedbackView(generics.CreateAPIView):
    """
    问答反馈接口（兼容旧接口）

    POST /api/chat/feedback/
    功能：用户根据点赞/点踩，写入 query_log.user_feedback
    参数：
        - message_id (int, required): 消息ID
        - feedback (int, required): 1=赞 / 0=踩
    返回：{"detail": "反馈已记录"}
    """
    serializer_class = FeedbackSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message_id = serializer.validated_data['message_id']
        feedback = serializer.validated_data['feedback']
        user = request.user

        # 找到消息，验证所有权
        msg = get_object_or_404(
            ChatMessage, id=message_id, conversation__user=user,
        )

        # 找到该会话最近一条 QueryLog，回填 user_feedback
        QueryLog.objects.filter(
            conversation=msg.conversation,
            user=user,
        ).order_by('-created_at').first()

        # 用 update 而非 save，避免触发 save() 逻辑
        updated = QueryLog.objects.filter(
            conversation=msg.conversation,
            user=user,
        ).order_by('-created_at').update(user_feedback=feedback)

        if not updated:
            logger.warning('FeedbackView: 未找到对应 QueryLog msg_id=%d user_id=%d', message_id, user.id)

        return Response(
            {'detail': '反馈已记录'},
            status=status.HTTP_200_OK,
        )
