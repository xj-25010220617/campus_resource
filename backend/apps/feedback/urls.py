from django.urls import path

from .views import FeedbackPendingListView, FeedbackAnswerView


urlpatterns = [
    path('pending/', FeedbackPendingListView.as_view(), name='feedback_pending'),
    path('<int:pk>/answer/', FeedbackAnswerView.as_view(), name='feedback_answer'),
]
