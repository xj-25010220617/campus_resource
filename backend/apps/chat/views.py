from rest_framework import generics, permissions
from .models import Conversation, ChatMessage, QueryLog
from .serializers import (
    ConversationSerializer,
    ConversationListSerializer,
    ChatMessageSerializer,
    AskSerializer,
    FeedbackSerializer,
)


class ConversationListView(generics.ListAPIView):
    """
    会话列表

    GET /api/chat/conversations/
    功能：返回当前用户的会话列表（不含消息详情），按更新时间倒序
    返回：分页的会话列表
    """
    serializer_class = ConversationListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).order_by('-updated_at')


class ConversationDetailView(generics.RetrieveAPIView):
    """
    会话详情

    GET /api/chat/conversations/<id>/
    功能：返回指定会话的完整消息列表（包含 AI 回答和用户提问）
    返回：会话对象，含 messages 数组
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
    问答反馈接口

    POST /api/chat/feedback/
    功能：用户对 AI 回答点赞/点踩，写入 query_log
    参数：
        - message_id (int, required): 消息ID
        - feedback (int, required): 1=赞 / 0=踩
    返回：{"detail": "反馈已记录"}
    """
    serializer_class = FeedbackSerializer
    permission_classes = [permissions.IsAuthenticated]
