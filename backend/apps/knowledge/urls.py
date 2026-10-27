from django.urls import path

from .chunked_views import (
    ChunkMergeView,
    ChunkUploadInitView,
    ChunkUploadView,
)
from .views import (
    AIClassifyView,
    BatchFileUploadView,
    ExtractFreshnessView,
    FileUploadView,
    KeywordCreateView,
    KeywordDeleteView,
    KeywordListView,
    KeywordUpdateView,
    KnowledgeDocBatchDeleteView,
    KnowledgeDocDeleteView,
    KnowledgeDocDetailView,
    KnowledgeDocDownloadView,
    KnowledgeDocListView,
    KnowledgeDocPreviewView,
    DocRelationDeleteView,
    DocRelationListView,
    DocTagCreateView,
    DocTagDeleteView,
    DocTagListView,
    TagDeleteView,
    TagListView,
    TagMergeView,
    TagRenameView,
    TextUploadView,
    UploadCallbackView,
    UploadSTSView,
)


urlpatterns = [
    # 上传
    path('upload/file/', FileUploadView.as_view(), name='upload_file'),
    path('upload/batch/', BatchFileUploadView.as_view(), name='upload_batch'),
    path('upload/text/', TextUploadView.as_view(), name='upload_text'),
    path('upload/sts/', UploadSTSView.as_view(), name='upload_sts'),
    path('upload/ai-classify/', AIClassifyView.as_view(), name='upload_ai_classify'),
    path('upload/classify/', AIClassifyView.as_view(), name='upload_classify'),
    path('upload/callback/', UploadCallbackView.as_view(), name='upload_callback'),

    # 分片上传（大文件）
    path('upload/chunk/init/', ChunkUploadInitView.as_view(), name='upload_chunk_init'),
    path('upload/chunk/', ChunkUploadView.as_view(), name='upload_chunk'),
    path('upload/chunk/merge/', ChunkMergeView.as_view(), name='upload_chunk_merge'),

    # 文档
    path('docs/', KnowledgeDocListView.as_view(), name='knowledge_doc_list'),
    path('docs/<int:pk>/', KnowledgeDocDetailView.as_view(), name='knowledge_doc_detail'),
    path('docs/batch/', KnowledgeDocBatchDeleteView.as_view(), name='knowledge_doc_batch_delete'),
    path('docs/<int:pk>/delete/', KnowledgeDocDeleteView.as_view(), name='knowledge_doc_delete'),
    path('docs/<int:pk>/download/', KnowledgeDocDownloadView.as_view(), name='knowledge_doc_download'),

    path('docs/<int:pk>/preview/', KnowledgeDocPreviewView.as_view(), name='knowledge_doc_preview'),
    path('docs/<int:pk>/extract-freshness/', ExtractFreshnessView.as_view(), name='extract_freshness'),

    # 关键词
    path('docs/<int:doc_id>/keywords/', KeywordListView.as_view(), name='keyword_list'),
    path('docs/<int:doc_id>/keywords/create/', KeywordCreateView.as_view(), name='keyword_create'),
    path('keywords/<int:pk>/', KeywordUpdateView.as_view(), name='keyword_update'),
    path('keywords/<int:pk>/delete/', KeywordDeleteView.as_view(), name='keyword_delete'),

    # 标签
    path('tags/', TagListView.as_view(), name='tag_list'),
    path('tags/<int:tag_id>/rename/', TagRenameView.as_view(), name='tag_rename'),
    path('tags/merge/', TagMergeView.as_view(), name='tag_merge'),
    path('tags/<int:tag_id>/delete/', TagDeleteView.as_view(), name='tag_delete'),
    path('docs/<int:doc_id>/tags/', DocTagListView.as_view(), name='doc_tag_list'),
    path('docs/<int:doc_id>/tags/create/', DocTagCreateView.as_view(), name='doc_tag_create'),
    path('docs/<int:doc_id>/tags/<int:tag_id>/delete/', DocTagDeleteView.as_view(), name='doc_tag_delete'),

    # 文档关联（文件网）
    path('docs/<int:doc_id>/relations/', DocRelationListView.as_view(), name='doc_relation_list'),
    path('docs/<int:doc_id>/relations/<int:relation_id>/delete/', DocRelationDeleteView.as_view(), name='doc_relation_delete'),
]
