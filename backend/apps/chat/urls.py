from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ConversationViewSet,
    MessageViewSet,
    ConversationListView,
    ConversationDetailView,
    AskView,
    FeedbackView,
)

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'message', MessageViewSet, basename='message')

urlpatterns = [
    # ViewSet 路由
    path('', include(router.urls)),

    # 旧接口兼容
    path('conversations-list/', ConversationListView.as_view(), name='conversation_list'),
    path('conversations/<int:pk>/detail/', ConversationDetailView.as_view(), name='conversation_detail'),

    # SSE 流式问答
    path('ask/', AskView.as_view(), name='chat_ask'),

    # 反馈接口
    path('feedback/', FeedbackView.as_view(), name='chat_feedback'),
]
