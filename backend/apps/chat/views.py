from rest_framework import generics, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.resources.models import Resource
from apps.resources.serializers import ResourceListSerializer

from .models import Conversation, ChatMessage, QueryLog
from .serializers import (
    ConversationSerializer,
    ConversationListSerializer,
    ChatMessageSerializer,
    AskSerializer,
    FeedbackSerializer,
    MessageRateSerializer,
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


class AskView(generics.CreateAPIView):
    """
    AI 问答接口（三层检索升级版）

    POST /api/chat/ask/
    功能：接收用户提问 → LLM 解析硬过滤 → SQL 缩范围 → 双路召回 → 权限校验 → 生成回答
    参数：
        - conversation_id (int, optional): 会话ID（不传则新建）
        - question (str, required): 用户提问
    返回：SSE 流式响应（text/event-stream）
    """
    serializer_class = AskSerializer
    permission_classes = [permissions.IsAuthenticated]


class FeedbackView(generics.CreateAPIView):
    """
    问答反馈接口（兼容旧接口）

    POST /api/chat/feedback/
    功能：用户根据点赞/点踩，写入 query_log
    参数：
        - message_id (int, required): 消息ID
        - feedback (int, required): 1=赞 / 0=踩
    返回：{"detail": "反馈已记录"}
    """
    serializer_class = FeedbackSerializer
    permission_classes = [permissions.IsAuthenticated]
