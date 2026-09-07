import logging
import time

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, parsers, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from apps.knowledge.models import KnowledgeDoc
from apps.knowledge.serializers import KnowledgeDocListSerializer
from apps.knowledge.views import KnowledgeDocListView

from utils import rag
from utils.llm import llm_client, LLMTimeoutError
from utils.sensitive_filter import check_sensitive
from utils.sse import _format_sse, build_images_events, build_references_events
from utils.error_codes import ErrorCode
from apps.classify.utils.query_parser import parse as parse_query

from .models import Conversation, ChatMessage, QueryLog, SensitiveLog
from .serializers import (
    AskSerializer, ChatMessageSerializer, ConversationListSerializer,
    ConversationRenameSerializer, ConversationSerializer,
    FeedbackSerializer, MessageRateSerializer, VoiceAskSerializer,
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

    def get_permissions(self):
        """create 允许匿名访问，其他操作需要登录"""
        if self.action == 'create':
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        if self.request.user.is_anonymous:
            return Conversation.objects.none()
        
        qs = Conversation.objects.filter(user=self.request.user)
        
        # 搜索功能：支持按标题或消息内容模糊搜索
        search_query = self.request.query_params.get('q', '').strip()
        if search_query:
            from django.db.models import Q
            # 搜索标题，同时搜索关联的消息内容
            qs = qs.filter(
                Q(title__icontains=search_query) |
                Q(messages__content__icontains=search_query)
            ).distinct()
        
        return qs.prefetch_related('messages')

    def get_serializer_class(self):
        if self.action == 'list':
            return ConversationListSerializer
        if self.action == 'partial_update':
            return ConversationRenameSerializer
        return ConversationSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user if self.request.user.is_authenticated else None)

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH /api/chat/conversations/{id}/
        重命名会话，仅返回 id、title、created_at、updated_at
        """
        instance = self.get_object()
        data = dict(request.data)
        # 空 title（纯问候语如"你好"被前端摘要逻辑剥空）视为"不修改"，
        # 避免 title 字段 required/不允许 blank 直接 400。
        title = data.get('title')
        if title is not None and str(title).strip() == '':
            data.pop('title', None)
        serializer = ConversationRenameSerializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)


@extend_schema(
    summary='消息管理',
    description='消息反馈和引用来源查看',
    tags=['AI对话'],
)
class MessageViewSet(ModelViewSet):
    """
    消息 ViewSet（用于消息反馈和引用来源查看）

    POST   /api/chat/message/{id}/rate/        - 消息反馈（点赞/点踩）
    GET    /api/chat/message/{id}/references/  - 查看引用来源（KnowledgeDoc + freshness_date）
    """
    queryset = ChatMessage.objects.all()
    serializer_class = ChatMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # 仅查询当前用户可访问的会话，避免通过消息 ID 跨会话读取引用。
        if self.request.user.is_anonymous:
            return ChatMessage.objects.none()
        return ChatMessage.objects.filter(
            conversation__user=self.request.user,
        ).select_related('conversation')

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

        # 更新 ChatMessage 的 rating 字段
        # rating='none' 表示取消反馈，置为 None
        if rating == 'none':
            message.rating = None
        else:
            message.rating = rating
        message.save(update_fields=['rating'])

        # 更新关联的 QueryLog — 取该会话最新一条（即本轮问答对应的查询日志）
        feedback_value = 1 if rating == 'like' else (0 if rating == 'dislike' else -1)
        QueryLog.objects.filter(
            conversation=message.conversation,
            user=request.user,
        ).order_by('-created_at').update(
            user_feedback=feedback_value,
        )

        return Response({'status': 'ok', 'rating': message.rating})

    @extend_schema(
        summary='查看消息引用来源',
        description=(
            '返回消息引用的知识文档列表（KnowledgeDoc），每条记录包含 '
            'KnowledgeDoc 元数据 + content_snippet + freshness_date（AI 提取的发布日期）。'
            'freshness_date 为 null 时，前端可继续调 '
            'POST /api/knowledge/docs/{doc_id}/extract-freshness/ 主动触发提取。'
        ),
        tags=['AI对话'],
    )
    @action(detail=True, methods=['get'], url_path='references')
    def references(self, request, pk=None):
        """
        GET /api/chat/message/{id}/references/
        查看引用来源文档（仅 admin/super_admin 可用）

        返回每条引用对应的 KnowledgeDoc 元数据 + AI 提取的发布日期，
        前端可在不调 extract-freshness 接口的情况下拿到 freshness_date。
        freshness_date 为 null 时，前端可继续调
        POST /api/knowledge/docs/{doc_id}/extract-freshness/ 主动触发提取。
        """
        # 权限检查
        if request.user.role not in ('admin', 'college_admin', 'dept_admin', 'super_admin'):
            return Response(
                {'detail': '仅管理员可查看引用来源'},
                status=status.HTTP_403_FORBIDDEN
            )

        message = self.get_object()
        references_detail = message.references or []

        if not references_detail:
            return Response([])

        # message.references 实际存的是 build_references_events 返回的
        # references_detail 列表（list[dict]），每个元素的 doc_id 是
        # KnowledgeDoc.id。为兼容历史纯 ID 列表的脏数据做一次判断。
        if isinstance(references_detail[0], dict):
            doc_ids = [r['doc_id'] for r in references_detail]
            snippet_map = {
                r['doc_id']: r.get('content_snippet', '')
                for r in references_detail
            }
        else:
            doc_ids = list(references_detail)
            snippet_map = {}

        # 查询 KnowledgeDoc（doc_id 即 KnowledgeDoc.id，不是 Resource.id）
        queryset = KnowledgeDocListView._admin_scope_filter(
            KnowledgeDoc.objects.filter(id__in=doc_ids, status='indexed'),
            request.user,
        )

        # 用列表序列化器作为基础字段，再叠加 content_snippet / freshness_date
        base_data = KnowledgeDocListSerializer(queryset, many=True).data
        doc_map = {item['id']: item for item in base_data}

        data = []
        for doc_id in doc_ids:
            item = doc_map.get(doc_id)
            if not item:
                continue
            doc = queryset.get(id=doc_id)
            item['content_snippet'] = snippet_map.get(doc_id, '')
            item['freshness_date'] = (
                doc.freshness_date.isoformat() if doc.freshness_date else None
            )
            data.append(item)

        return Response(data)


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
        - event: suggested      → data: [{"display": "引导句", "question": "干净问句"}, ...]
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
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        question = serializer.validated_data['question']
        conversation_id = serializer.validated_data.get('conversation_id')

        # 处理匿名用户
        user = request.user if request.user.is_authenticated else None
        user_id = user.id if user else None
        user_role = user.role if user else 'user'
        user_context = {
            'user_id': user_id,
            'role': user_role,
            'college_id': getattr(user, 'college_id', None),
            'department_id': getattr(user, 'department_id', None),
        }

        # 0. 敏感内容检查
        sensitive_result = check_sensitive(question)
        if sensitive_result.is_sensitive:
            logger.warning(
                '敏感内容拦截: user=%s question=%s... rules=%s',
                user.username if user else 'anonymous', question[:60], sensitive_result.matched_rules,
            )
            SensitiveLog.objects.create(
                user=user,
                question=question,
                matched_rules=sensitive_result.matched_rules,
                matched_texts=sensitive_result.matched_texts,
                refuse_message=sensitive_result.refuse_message,
            )
            return Response(
                {
                    'detail': sensitive_result.refuse_message,
                    'code': ErrorCode.SENSITIVE_CONTENT.value,
                    'matched_rules': sensitive_result.matched_rules,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 0.5 实时网页分析 / 联网搜索
        import re as _re
        _url = _re.search(r'https?://[^\s,，。；;]+', question)
        if not _url:
            _url = _re.search(r'(www\.nsu\.edu\.cn[\S]*)', question)
        if not _url:
            # 放宽 topic 拦截：只有当问题明确要求"最新/当前/实时"信息时才劫持到联网搜索
            # 避免用户问关于"图书馆"的知识库内容也被强制联网
            _topic_map = {'通知公告': '', '公告': '', '校园时讯': '', '新闻': '', '学校概况': '', '招生就业': '', '图书馆': ''}
            _fresh_keywords = ['最新', '今天', '最近', '当前', '实时', '现', '即时']
            has_fresh_intent = any(kw in question for kw in _fresh_keywords)

            if has_fresh_intent:
                for _topic in _topic_map:
                    if _topic in question:
                        return self._handle_search_query(question, user, conversation_id, _topic)
        if _url:
            return self._handle_web_query(question, user, conversation_id, _url.group())

        # 0.6 意图路由：判断问题类型（文件管理查询 / 常规 RAG）
        from utils.query_router import route_question
        from utils.data_query import execute_query, build_data_query_prompt
        route = route_question(question, user)
        is_data_query = route['type'] == 'data_query'

        # 匿名用户不支持文件管理查询
        if is_data_query and not user:
            return Response(
                {'detail': '请先登录以使用查询功能', 'code': ErrorCode.UNAUTHORIZED.value},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 1. 获取或新建会话（匿名用户只能查自己刚创建 user=null 的对话）
        if conversation_id:
            if not user:
                conversation = get_object_or_404(
                    Conversation, id=conversation_id, user__isnull=True,
                )
            else:
                conversation = get_object_or_404(
                    Conversation, id=conversation_id, user=user,
                )
        else:
            conversation = Conversation.objects.create(
                user=user, title=question[:50],
            )
            logger.info('新建会话 id=%d user=%s', conversation.id, user.username if user else 'anonymous')

        # 2. 保存用户消息
        ChatMessage.objects.create(
            conversation=conversation, role='user', content=question,
        )

        # 文件管理查询路由
        if is_data_query:
            return self._handle_data_query(
                question, user, conversation, route,
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

        # 3.1.5 FAQ 短路：高频问题走 FAQ 表（如果有）
        # 收紧条件：仅对非常短的问题（<=15 字）且 FAQ 问题也较短（<=25 字）时才短路
        try:
            from apps.faq.models import FAQ
            from django.db.models import Q
            from django.db.models.functions import Length
            if len(question) <= 15:
                # 只匹配问题长度相近的 FAQ，避免用长 FAQ 匹配短问题
                faq_hit = FAQ.objects.filter(
                    Q(question__iexact=question) | Q(question__contains=question),
                ).annotate(q_len=Length('question')).filter(q_len__lte=25).first()
                if faq_hit:
                    logger.info('FAQ 短路命中: q=%s faq_id=%d', question[:30], faq_hit.id)
                    return Response({
                        'answer': faq_hit.answer,
                        'source': 'faq',
                        'faq_id': faq_hit.id,
                    })
        except (ImportError, AttributeError, Exception):
            pass  # 没有 FAQ 表或字段不匹配，静默跳过

        # 3.2 RAG 检索（新版：硬过滤 + 双路召回 + 权限校验）
        results = rag.retrieve(
            question=question,
            user_context=user_context,
            parsed_filters=parsed,
        )

        # 3.2b 知识库无结果 → 联网搜索兜底
        web_search_used = False
        if not results:
            logger.info('知识库无结果，启动联网搜索兜底: %s', question[:50])
            from utils.web_search import search_school
            search_results = search_school(question, max_results=5)
            if search_results:
                web_search_used = True
                for sr in search_results:
                    results.append({
                        'doc_id': 0,
                        'doc_title': sr['title'],
                        'content': (
                            f"标题：{sr['title']}\n"
                            f"摘要：{sr['snippet']}\n"
                            f"来源：{sr['url']}"
                        ),
                        'score': 0.6,
                        'page': 0,
                        'source_type': 'web_search',
                    })
                logger.info('联网搜索兜底命中 %d 条', len(results))
            else:
                logger.info('联网搜索也无结果')

        # 3.2c 图片检索（用于回答内联展示相关图片）
        image_intent = rag._detect_image_intent(question)
        images = rag.retrieve_images(
            question=question,
            user_context=user_context,
            parsed_filters=parsed,
            top_k=8 if image_intent else 4,
        )
        images_events = build_images_events(images)  # 预计算，保持流内无异常

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
        llm_question = question
        if image_intent and images:
            titles = '、'.join(img['doc_title'] for img in images[:6])
            llm_question = (
                f'{question}\n\n'
                f'（系统已为你检索到相关图片：{titles}。请在回答末尾说明"已为你找到相关图片，详见下方"。）'
            )
        messages = rag.build_rag_prompt(llm_question, results, history=history)

        # 6. 创建 AI 消息占位（流结束后写入完整内容）
        is_authorized = user_role in ('admin', 'super_admin')
        references_detail = build_references_events(
            results, user_role, user_id, user_context=user_context,
        ).get('references_detail', [])

        assistant_msg = ChatMessage.objects.create(
            conversation=conversation, role='assistant',
            content='', references=references_detail,
        )

        # 7. SSE 流式响应
        def event_stream():
            """生成 SSE 事件流（finally 保证 query_log 回填；断连时把已收内容落库后结束）。"""
            collected_tokens = []
            saved_len = 0

            def _persist_partial():
                """把当前已生成内容增量落库，避免客户端断连/切走后回答丢失。"""
                nonlocal saved_len
                content = ''.join(collected_tokens)
                if len(content) > saved_len:
                    ChatMessage.objects.filter(id=assistant_msg.id).update(content=content)
                    saved_len = len(content)

            try:
                # start 事件
                yield _format_sse('start', {
                    'conversation_id': conversation.id,
                    'message_id': assistant_msg.id,
                })

                # 流式生成
                stream = llm_client.chat_stream(messages)
                for token in stream:
                    if token:
                        collected_tokens.append(token)
                        yield _format_sse('token', {'content': token})
                        # 每 20 token 增量落库，防止切走/断连后内容丢失
                        if len(collected_tokens) % 20 == 0:
                            _persist_partial()

                full_answer = ''.join(collected_tokens)

                # 保存 AI 回答（流结束后写库）
                if full_answer:
                    _persist_partial()
                    logger.info(
                        'AI 回答完成 msg_id=%d conv_id=%d tokens=%d',
                        assistant_msg.id, conversation.id, len(collected_tokens),
                    )

                # 生成相关推荐（调用 LLM 生成 3 个相关问题）
                suggested = rag.generate_suggested_questions(question, full_answer)
                if suggested:
                    yield _format_sse('suggested', suggested)

                # 引用溯源
                refs = build_references_events(
                    results, user_role, user_id, user_context=user_context,
                )
                if 'references' in refs:
                    yield _format_sse('references', refs['references'])
                if 'references_detail' in refs and is_authorized:
                    yield _format_sse('references_detail', refs['references_detail'])

                # 图片（内联展示）
                if images_events.get('images'):
                    yield _format_sse('images', images_events['images'])

                # done 事件
                yield _format_sse('done', {'message_id': assistant_msg.id})

            except GeneratorExit:
                # 客户端断开：把已收到的部分内容落库后直接结束。
                # 后台续收由前端方案 A 覆盖（切换时不 abort，SSE 后台继续收），后端不再兜底拉完。
                _persist_partial()
                raise
            except LLMTimeoutError as e:
                # AI 回答超时：下发明确提示 + 已检索到的图片（让图片不因超时而丢失）
                logger.warning(f'AI 回答超时: {e}')
                _persist_partial()
                yield _format_sse('error', {'message': 'AI回答超时，请稍后重试', 'code': ErrorCode.SSE_ERROR.value})
                if images_events.get('images'):
                    yield _format_sse('images', images_events['images'])
            except Exception as e:
                logger.error(f'SSE 流异常: {e}')
                _persist_partial()
                yield _format_sse('error', {'message': '回答生成失败，请重试', 'code': ErrorCode.SSE_ERROR.value})
            finally:
                # 兜底落库 + 保证流中断时 response_ms 也能回填（客户端断连、Django 回收 generator 时触发）
                try:
                    _persist_partial()
                except Exception:
                    pass
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

    def _handle_data_query(self, question, user, conversation, route):
        """处理文件管理类查询：查询数据库 → LLM 格式化 → SSE 流式输出。"""
        from utils.data_query import execute_query, build_data_query_prompt
        from utils.sse import _format_sse

        # 执行查询
        query_result = execute_query(route, user)

        # 构造 Prompt
        messages = build_data_query_prompt(question, query_result)

        # 创建 AI 消息占位
        assistant_msg = ChatMessage.objects.create(
            conversation=conversation, role='assistant',
            content='',
        )

        # SSE 流式响应
        def event_stream():
            try:
                yield _format_sse('start', {
                    'conversation_id': conversation.id,
                    'message_id': assistant_msg.id,
                })

                collected = []
                for token in llm_client.chat_stream(messages, temperature=0.3, max_tokens=1024):
                    if token:
                        collected.append(token)
                        yield _format_sse('token', {'content': token})

                full_answer = ''.join(collected)
                if full_answer:
                    ChatMessage.objects.filter(id=assistant_msg.id).update(
                        content=full_answer,
                    )

                suggested = rag.generate_suggested_questions(question, full_answer)
                if suggested:
                    yield _format_sse('suggested', suggested)

                yield _format_sse('done', {'message_id': assistant_msg.id})

            except GeneratorExit:
                raise
            except Exception as e:
                logger.error(f'数据查询 SSE 流输出失败: {e}')
                yield _format_sse('error', {'message': '查询失败，请重试', 'code': ErrorCode.SSE_ERROR.value})

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        response['Access-Control-Allow-Origin'] = '*'
        return response

    def _handle_web_query(self, question, user, conversation_id, url, topic=''):
        """实时抓取网页 → LLM 分析 → SSE 流式输出（不存储）。"""
        from utils.web_fetcher import fetch_page
        from utils.sse import _format_sse

        # 联网抓取为 SSRF 敏感路径：必须登录后才允许触发
        if not user:
            return Response(
                {'detail': '请先登录以使用联网查询功能', 'code': ErrorCode.UNAUTHORIZED.value},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 抓取
        page_text = fetch_page(url)
        if not page_text or len(page_text) < 500:
            # 网页抓取失败或内容太少（JS渲染页），走内建知识库
            logger.info(f'网页内容不足({len(page_text) if page_text else 0}字)，走内建知识库')
            return self._fallback_to_knowledge(question, user, conversation_id, topic or url)

        # 会话归属校验：与主路径 AskView 一致，仅允许自己的会话
        if conversation_id:
            conv = get_object_or_404(Conversation, id=conversation_id, user=user)
        else:
            conv = Conversation.objects.create(user=user, title=f'网页分析: {url[:40]}')
        ChatMessage.objects.create(conversation=conv, role='user', content=question)
        assistant_msg = ChatMessage.objects.create(
            conversation=conv, role='assistant', content='',
        )

        # 构造 Prompt：把网页内容作为上下文给 LLM
        from utils.context import build_system_context
        source_desc = f'（{topic}）' if topic else ''
        system_prompt = (
            f'{build_system_context()}\n\n'
            '你是一个网站内容分析助手。用户要求查看网站内容，你已实时获取了该页面的内容。'
            '请阅读并回答用户的问题。\n\n'
            f'网页地址：{url} {source_desc}\n'
            f'网页内容：\n---\n{page_text}\n---\n\n'
            '请根据获取到的网页内容直接回答。如果页面内容是导航菜单而非具体文章，'
            '请如实告知用户该页面上有哪些栏目或链接可供查看。'
        )

        def event_stream():
            try:
                yield _format_sse('start', {
                    'conversation_id': conv.id, 'message_id': assistant_msg.id,
                })
                full_answer = []
                for token in llm_client.chat_stream(
                    [{'role': 'system', 'content': system_prompt},
                     {'role': 'user', 'content': question}],
                    temperature=0.3, max_tokens=2048,
                ):
                    if token:
                        full_answer.append(token)
                        yield _format_sse('token', {'content': token})
                yield _format_sse('references', {'count': 1, 'summary': '实时抓取: ' + url})
                suggested = rag.generate_suggested_questions(question, ''.join(full_answer))
                if suggested:
                    yield _format_sse('suggested', suggested)
                yield _format_sse('done', {'message_id': assistant_msg.id})
            except Exception as e:
                logger.error(f'网页分析 SSE 异常: {e}')
                yield _format_sse('error', {'message': '分析失败，请重试'})

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        response['Access-Control-Allow-Origin'] = '*'
        return response

    def _fallback_to_knowledge(self, question, user, conversation_id, topic):
        """网页抓取失败时，用内建知识库回答。"""
        from utils.sse import _format_sse
        from utils.rag import NEUSOFT_KNOWLEDGE
        from utils.context import build_system_context

        # 会话归属校验：与主路径 AskView 一致，仅允许自己的会话
        if conversation_id:
            conv = get_object_or_404(Conversation, id=conversation_id, user=user)
        else:
            conv = Conversation.objects.create(user=user, title=f'学校咨询: {question[:40]}')
        ChatMessage.objects.create(conversation=conv, role='user', content=question)
        assistant_msg = ChatMessage.objects.create(conversation=conv, role='assistant', content='')

        prompt = (
            f'{build_system_context()}\n\n'
            '你是成都东软学院的AI智能助手。用户想了解学校信息，'
            '但学校官网是JS动态渲染的，无法直接抓取内容。\n\n'
            f'请使用以下内建知识库回答：\n---\n{NEUSOFT_KNOWLEDGE}\n---\n\n'
            '如果内建知识库不足以回答，请如实告知并建议访问官网 https://www.nsu.edu.cn。'
        )

        def _stream():
            try:
                yield _format_sse('start', {'conversation_id': conv.id, 'message_id': assistant_msg.id})
                full_answer = []
                for token in llm_client.chat_stream(
                    [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': question}],
                    temperature=0.3, max_tokens=1024,
                ):
                    if token:
                        full_answer.append(token)
                        yield _format_sse('token', {'content': token})
                yield _format_sse('references', {'count': 1, 'summary': '内建知识库'})
                suggested = rag.generate_suggested_questions(question, ''.join(full_answer))
                if suggested:
                    yield _format_sse('suggested', suggested)
                yield _format_sse('done', {'message_id': assistant_msg.id})
            except Exception as e:
                logger.error(f'知识库SSE异常: {e}')
                yield _format_sse('error', {'message': '查询失败'})

        response = StreamingHttpResponse(_stream(), content_type='text/event-stream')
        response['Access-Control-Allow-Origin'] = '*'
        return response

    def _handle_search_query(self, question, user, conversation_id, topic):
        """联网搜索 → LLM 分析 → SSE 输出。"""
        from utils.web_search import search_school
        from utils.sse import _format_sse
        from utils.context import build_system_context

        results = search_school(question)
        # 会话归属校验：与主路径 AskView 一致，仅允许自己的会话
        if conversation_id:
            conv = get_object_or_404(Conversation, id=conversation_id, user=user)
        else:
            conv = Conversation.objects.create(user=user, title=f'搜索: {question[:40]}')
        ChatMessage.objects.create(conversation=conv, role='user', content=question)
        msg = ChatMessage.objects.create(conversation=conv, role='assistant', content='')

        if not results:
            sp = f'{build_system_context()}\n\n你搜索了 "%s" 但没有找到结果，请如实告知。' % question
        else:
            lines = '\n'.join(
                '%d. [%s](%s)\n   %s' % (i+1, r['title'], r['url'], r['snippet'])
                for i, r in enumerate(results)
            )
            sp = (
                f'{build_system_context()}\n\n'
                '以下是通过搜索引擎找到的成都东软学院相关信息。请阅读并整理回答。\n\n'
                '%s\n\n请列出信息来源链接。' % lines
            )

        def _s():
            try:
                yield _format_sse('start', {'conversation_id': conv.id, 'message_id': msg.id})
                full_answer = []
                for t in llm_client.chat_stream(
                    [{'role': 'system', 'content': sp}, {'role': 'user', 'content': question}],
                    temperature=0.3, max_tokens=1024,
                ):
                    if t:
                        full_answer.append(t)
                        yield _format_sse('token', {'content': t})
                yield _format_sse('references', {'count': len(results), 'summary': '联网搜索'})
                suggested = rag.generate_suggested_questions(question, ''.join(full_answer))
                if suggested:
                    yield _format_sse('suggested', suggested)
                yield _format_sse('done', {'message_id': msg.id})
            except Exception as e:
                logger.error(f'搜索SSE异常: {e}')
                yield _format_sse('error', {'message': '搜索失败'})

        r = StreamingHttpResponse(_s(), content_type='text/event-stream')
        r['Cache-Control'] = 'no-cache'; r['X-Accel-Buffering'] = 'no'; r['Access-Control-Allow-Origin'] = '*'
        return r


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


class VoiceAskView(APIView):
    """
    语音问答接口

    POST /api/chat/voice-ask/
    功能：接收音频文件 → FunASR 语音转文字 → RAG 检索 → LLM 流式 SSE 回答
    请求参数（form-data）：
        - audio_file (file, required): 音频文件，支持 WAV/MP3/PCM，最大 10MB
        - conversation_id (int, optional): 会话 ID，不传则新建
    返回：SSE 流式响应（text/event-stream）
    错误：
        - 400: 文件无效
        - 500: 语音识别失败
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def post(self, request):
        """处理语音问答"""
        from utils.asr import speech_to_text

        serializer = VoiceAskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 读取音频文件
        audio_file = data['audio_file']
        if audio_file.size > 10 * 1024 * 1024:
            return Response(
                {'detail': '音频文件不能超过 10MB', 'code': ErrorCode.FILE_TOO_LARGE.value},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audio_data = audio_file.read()

        # 从 MIME type 提取格式（如 "audio/webm;codecs=opus" → "webm"）
        # 备选：从文件名扩展名（如 "recording.webm" → "webm"）
        # 最终由 asr.speech_to_text() 内部魔数检测兜底
        mime_type = getattr(audio_file, 'content_type', '') or ''
        if mime_type:
            # 取 "audio/webm;codecs=opus" → "webm"
            audio_fmt = mime_type.split(';')[0].split('/')[-1].strip()
        elif '.' in audio_file.name:
            audio_fmt = audio_file.name.rsplit('.', 1)[-1]
        else:
            audio_fmt = 'wav'

        text, asr_error = speech_to_text(audio_data, audio_fmt)
        if not text:
            # 映射 asr_error.error_type → 对应 ErrorCode
            asr_code_map = {
                'PYDUB_NOT_INSTALLED': ErrorCode.ASR_PYDUB_NOT_INSTALLED,
                'FFMPEG_NOT_FOUND': ErrorCode.ASR_FFMPEG_NOT_FOUND,
                'FORMAT_CONVERT_FAILED': ErrorCode.ASR_FORMAT_CONVERT_FAILED,
                'AUDIO_TOO_SMALL': ErrorCode.ASR_AUDIO_TOO_SMALL,
                'AUDIO_TOO_LARGE': ErrorCode.ASR_AUDIO_TOO_LARGE,
                'AUDIO_CONVERT_FAILED': ErrorCode.ASR_AUDIO_CONVERT_FAILED,
                'ASR_MODEL_NOT_LOADED': ErrorCode.ASR_MODEL_NOT_LOADED,
                'ASR_EMPTY_RESULT': ErrorCode.ASR_EMPTY_RESULT,
                'ASR_NO_TEXT_EXTRACTED': ErrorCode.ASR_NO_TEXT_EXTRACTED,
                'ASR_UNKNOWN_ERROR': ErrorCode.ASR_UNKNOWN_ERROR,
            }
            error_code = asr_code_map.get(
                asr_error.get('error_type'), ErrorCode.ASR_FAILED,
            ) if asr_error else ErrorCode.ASR_FAILED

            # 构建详细错误响应（含错误阶段、类型、建议、完整处理日志）
            error_response = {
                'detail': asr_error['detail'] if asr_error else '语音识别失败，请确认音频清晰且包含中文语音',
                'code': error_code.value,
            }
            if asr_error:
                error_response['asr_error'] = {
                    'stage': asr_error.get('stage'),
                    'error_type': asr_error.get('error_type'),
                    'suggestion': asr_error.get('suggestion'),
                    'error_log': asr_error.get('error_log', []),
                }
            logger.warning(
                '语音识别失败: stage=%s type=%s detail=%s',
                asr_error.get('stage') if asr_error else 'unknown',
                asr_error.get('error_type') if asr_error else 'unknown',
                asr_error.get('detail') if asr_error else 'unknown',
            )
            return Response(
                error_response,
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 复用 AskView 的 RAG 检索 + LLM 流式逻辑
        return self._stream_answer(text, request.user, data.get('conversation_id'))

    def _stream_answer(self, question: str, user, conversation_id):
        """RAG 检索 + LLM 流式 SSE，复用 AskView 核心逻辑"""
        from django.http import StreamingHttpResponse
        from utils.sse import _format_sse, build_references_events

        # 敏感内容拦截：与文本通道 AskView 一致，防止经语音通道绕过
        sensitive_result = check_sensitive(question)
        if sensitive_result.is_sensitive:
            SensitiveLog.objects.create(
                user=user,
                question=question,
                matched_rules=sensitive_result.matched_rules,
                matched_texts=sensitive_result.matched_texts,
                refuse_message=sensitive_result.refuse_message,
            )
            return Response(
                {
                    'detail': sensitive_result.refuse_message,
                    'code': ErrorCode.SENSITIVE_CONTENT.value,
                    'matched_rules': sensitive_result.matched_rules,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_context = {
            'user_id': user.id, 'role': user.role,
            'college_id': user.college_id, 'department_id': user.department_id,
        }
        parsed = parse_query(question, user_context)

        # 处理或创建会话
        if conversation_id:
            conv = get_object_or_404(Conversation, id=conversation_id, user=user)
        else:
            conv = Conversation.objects.create(user=user, title=question[:50])
        ChatMessage.objects.create(conversation=conv, role='user', content=question)

        # 写查询日志（先创建，hits 在 generate() 内 RAG 检索后回填）
        log = QueryLog.objects.create(
            user=user, question=question, parsed_filters=parsed,
            hit_count=0, top_doc_ids=[],
            conversation=conv,
        )

        ai_msg_ref = [None]  # mutable holder, set after stream

        def generate():
            full_answer = ''
            t0 = time.time()
            try:
                # 1. 推送 ASR 识别结果（语音转文字）
                yield _format_sse('asr_text', {'text': question})

                # 2. RAG 检索（在 asr_text 推送之后执行）
                hits = rag.retrieve(
                    question=question, user_context=user_context,
                    parsed_filters=parsed, top_k=rag.RAG_TOP_K,
                    min_score=rag.RAG_SCORE_THRESHOLD,
                )
                messages = rag.build_rag_prompt(question, hits)

                # 更新查询日志的 hits 信息
                log.hit_count = len(hits)
                log.top_doc_ids = [h['doc_id'] for h in hits]

                # 3. LLM 流式输出
                for chunk in llm_client.chat_stream(messages):
                    full_answer += chunk
                    yield _format_sse('token', {'content': chunk})
                references = build_references_events(
                    hits, user.role, user.id, user_context=user_context,
                )
                persisted_refs = references.get('references_detail', [])
                ai_msg = ChatMessage.objects.create(
                    conversation=conv, role='assistant', content=full_answer,
                    references=persisted_refs,
                )
                ai_msg_ref[0] = ai_msg
                log.response_ms = int((time.time() - t0) * 1000)
                log.save()
                if 'references' in references:
                    yield _format_sse('references', references['references'])
                if 'references_detail' in references and user.role in ('admin', 'super_admin'):
                    yield _format_sse('references_detail', references['references_detail'])
                yield _format_sse('done', {'message_id': ai_msg.id})
            except GeneratorExit:
                raise
            except Exception as e:
                logger.error(f'LLM 流式输出失败: {e}')
                log.response_ms = int((time.time() - t0) * 1000)
                try:
                    log.save()
                except Exception:
                    pass
                yield _format_sse('error', {'message': '回答生成失败，请重试', 'code': ErrorCode.SSE_ERROR.value})

        response = StreamingHttpResponse(generate(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        response['X-Message-Id'] = str(log.id)
        return response
class HotQuestionsView(APIView):
    """
    热点问题广场 — "大家都在问"

    GET /api/chat/hot-questions/?days=7&top_k=10

    实时统计近期用户高频提问，按提问次数降序排列。
    自动过滤与学习/学校/资料无关的噪音内容（如纯问候、测试消息、无意义短文本等）。
    适合展示在智能问答主界面。
    """
    permission_classes = [permissions.AllowAny]

    # 排除关键词（不区分大小写）：包含这些关键词的内容将被过滤掉
    EXCLUDE_KEYWORDS = [
        # 无意义闲聊/问候
        '你好', '早上好', '下午好', '晚上好', '大家好',
        '哈哈哈', '呵呵', '嘿嘿', '嘻嘻', '嘿嘿嘿',
        '再见', '拜拜', '88',
        # 垃圾/测试内容
        '测试', 'test', 'ceshi', 'asdf', 'qwerty',
        # 无意义数字/符号
        '111', '222', '333', '123', '1234', '12345',
        '999', '000',
        # 无关话题
        '一加一', '1+1', '2+2', '3+3',
        '生日快乐', 'happy birthday', '新年快乐', '节日快乐',
        '天气', '天气预报',
        # 身份询问
        '你是谁', '你叫什么', '谁是你',
        # 无意义重复字符序列
        '一二三', '一二三四五',
    ]

    # 仅当内容长度超过此值时计入热点（单位：字符）
    MIN_CONTENT_LENGTH = 4

    @staticmethod
    def _is_noise(content: str) -> bool:
        """Python 层二次过滤：检测 SQL 层面难以表达的噪音模式"""
        # 纯数字/符号/字母（无中文字符的学习相关内容）
        import re
        stripped = content.strip()
        # 去掉常见标点后检查是否全是非中文字符
        no_punct = re.sub(r'[，。！？、；：""''（）\-\s\d\w]', '', stripped)
        if no_punct and all('一' <= c <= '鿿' for c in no_punct):
            # 有中文，检查是否重复模式：去重后字符太少 → 反复重复几个字
            unique_chars = set(no_punct)
            if len(unique_chars) <= 2 and len(no_punct) >= 4:
                return True
        return False

    def get(self, request, *args, **kwargs):
        days = int(request.query_params.get('days', 7))
        top_k = int(request.query_params.get('top_k', 10))

        since = timezone.now() - timezone.timedelta(days=days)

        from django.db.models import Count, Q

        # 构建排除条件：内容包含任一排除关键词
        exclude_q = Q()
        for keyword in self.EXCLUDE_KEYWORDS:
            exclude_q |= Q(content__icontains=keyword)

        # 构建长度过滤条件：太短的文本（纯表情、单个词）视为无意义
        from django.db.models.functions import Length

        questions_qs = (
            ChatMessage.objects
            .filter(role='user', created_at__gte=since)
            .annotate(content_len=Length('content'))
            .filter(content_len__gte=self.MIN_CONTENT_LENGTH)
            .exclude(exclude_q)
            .values('content')
            .annotate(count=Count('id'))
            .order_by('-count')[:top_k * 2]  # 多取一些，留给 Python 层二次过滤
        )

        results = []
        for item in questions_qs:
            if len(results) >= top_k:
                break
            if self._is_noise(item['content']):
                continue
            results.append({
                'question': item['content'],
                'count': item['count'],
            })

        return Response(results)
