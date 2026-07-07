from django.urls import path

from .views import (
    FAQCategoryListView,
    FAQItemListView,
    FAQItemDetailView,
    FAQDraftListView,
    FAQDraftDetailView,
    FAQDraftActionView,
    FAQManageListView,
    FAQManageDeleteView,
    FAQGenerationLogListView,
    FAQGenerationLogDetailView,
    FAQGenerateTriggerView,
)

urlpatterns = [
    # 公共接口
    path('faq/categories/', FAQCategoryListView.as_view(), name='faq_categories'),
    path('faq/items/', FAQItemListView.as_view(), name='faq_items'),
    path('faq/items/<int:pk>/', FAQItemDetailView.as_view(), name='faq_item_detail'),

    # 管理接口
    path('faq/drafts/', FAQDraftListView.as_view(), name='faq_drafts'),
    path('faq/drafts/<int:pk>/', FAQDraftDetailView.as_view(), name='faq_draft_detail'),
    path('faq/drafts/<int:pk>/action/', FAQDraftActionView.as_view(), name='faq_draft_action'),

    # 管理端全量操作
    path('faq/manage/items/', FAQManageListView.as_view(), name='faq_manage_items'),
    path('faq/manage/items/<int:pk>/', FAQManageDeleteView.as_view(), name='faq_manage_item_delete'),

    # 手动触发 FAQ 生成（仅 super_admin）
    path('faq/generate/', FAQGenerateTriggerView.as_view(), name='faq_generate_trigger'),

    # FAQ 生成日志（管理端查看）
    path('faq/generation-logs/', FAQGenerationLogListView.as_view(), name='faq_generation_logs'),
    path('faq/generation-logs/<int:pk>/', FAQGenerationLogDetailView.as_view(), name='faq_generation_log_detail'),
]     
