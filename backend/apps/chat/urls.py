from django.urls import path
from .views import ConversationListView, ConversationDetailView, AskView, FeedbackView

urlpatterns = [
    path('conversations/', ConversationListView.as_view(), name='conversation_list'),
    path('conversations/<int:pk>/', ConversationDetailView.as_view(), name='conversation_detail'),
    path('ask/', AskView.as_view(), name='chat_ask'),
    path('feedback/', FeedbackView.as_view(), name='chat_feedback'),
]
