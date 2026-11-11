from django.urls import path

from .chunked_views import (
    ChunkMergeView,
    ChunkUploadInitView,
    ChunkUploadView,
)
from .folder_views import (
    DocBatchMoveView,
    DocMoveView,
    FolderDetailView,
    FolderListCreateView,
)
from .review_views import (
    DocReviewApproveView,
    DocReviewHistoryView,
    DocReviewRejectView,
    DocReviewResubmitView,
    PendingReviewListView,
)
from .version_views import (
    VersionCandidateConfirmView,
    VersionCandidateListView,
    VersionCandidateRejectView,
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

    # 文档审核（reviewer 专用；super_admin 不参与审核）
    path('reviews/pending/', PendingReviewListView.as_view(), name='doc_review_pending'),
    path('reviews/history/', DocReviewHistoryView.as_view(), name='doc_review_history'),
    path('docs/<int:pk>/review/approve/', DocReviewApproveView.as_view(), name='doc_review_approve'),
    path('docs/<int:pk>/review/reject/', DocReviewRejectView.as_view(), name='doc_review_reject'),
    path('docs/<int:pk>/review/resubmit/', DocReviewResubmitView.as_view(), name='doc_review_resubmit'),

    # 部门资料目录
    path('folders/', FolderListCreateView.as_view(), name='knowledge_folder_list'),
    path('folders/<int:pk>/', FolderDetailView.as_view(), name='knowledge_folder_detail'),
    path('docs/batch-move/', DocBatchMoveView.as_view(), name='knowledge_doc_batch_move'),

    # 文档
    path('docs/', KnowledgeDocListView.as_view(), name='knowledge_doc_list'),
    path('docs/<int:pk>/', KnowledgeDocDetailView.as_view(), name='knowledge_doc_detail'),
    path('docs/batch/', KnowledgeDocBatchDeleteView.as_view(), name='knowledge_doc_batch_delete'),
    path('docs/<int:pk>/delete/', KnowledgeDocDeleteView.as_view(), name='knowledge_doc_delete'),
    path('docs/<int:pk>/move/', DocMoveView.as_view(), name='knowledge_doc_move'),
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

    # 新旧版本候选（上传者确认后旧版才从问答检索中隔离）
    path('version-candidates/', VersionCandidateListView.as_view(), name='version_candidate_list'),
    path('version-candidates/<int:pk>/confirm/', VersionCandidateConfirmView.as_view(), name='version_candidate_confirm'),
    path('version-candidates/<int:pk>/reject/', VersionCandidateRejectView.as_view(), name='version_candidate_reject'),
]
