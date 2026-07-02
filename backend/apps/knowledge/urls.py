from django.urls import path

from .views import (
    ExtractFreshnessView,
    FileUploadView,
    KeywordCreateView,
    KeywordDeleteView,
    KeywordListView,
    KeywordUpdateView,
    KnowledgeDocDeleteView,
    KnowledgeDocDetailView,
    KnowledgeDocDownloadView,
    KnowledgeDocListView,
    KnowledgeDocPreviewView,
    TextUploadView,
    UploadCallbackView,
    UploadSTSView,
)


urlpatterns = [
    # 上传
    path('upload/file/', FileUploadView.as_view(), name='upload_file'),
    path('upload/text/', TextUploadView.as_view(), name='upload_text'),
    path('upload/sts/', UploadSTSView.as_view(), name='upload_sts'),
    path('upload/callback/', UploadCallbackView.as_view(), name='upload_callback'),

    # 文档
    path('docs/', KnowledgeDocListView.as_view(), name='knowledge_doc_list'),
    path('docs/<int:pk>/', KnowledgeDocDetailView.as_view(), name='knowledge_doc_detail'),
    path('docs/<int:pk>/delete/', KnowledgeDocDeleteView.as_view(), name='knowledge_doc_delete'),
    path('docs/<int:pk>/download/', KnowledgeDocDownloadView.as_view(), name='knowledge_doc_download'),

    path('docs/<int:pk>/preview/', KnowledgeDocPreviewView.as_view(), name='knowledge_doc_preview'),
    path('docs/<int:pk>/extract-freshness/', ExtractFreshnessView.as_view(), name='extract_freshness'),

    # 关键词
    path('docs/<int:doc_id>/keywords/', KeywordListView.as_view(), name='keyword_list'),
    path('docs/<int:doc_id>/keywords/create/', KeywordCreateView.as_view(), name='keyword_create'),
    path('keywords/<int:pk>/', KeywordUpdateView.as_view(), name='keyword_update'),
    path('keywords/<int:pk>/delete/', KeywordDeleteView.as_view(), name='keyword_delete'),
]
