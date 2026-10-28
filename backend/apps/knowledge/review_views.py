"""知识文档审核接口。

- GET  /api/knowledge/reviews/pending/           待审列表（按审核员归属自动过滤）
- GET  /api/knowledge/reviews/history/           本人已审记录
- POST /api/knowledge/docs/{id}/review/approve/  通过（通过后才派发索引任务）
- POST /api/knowledge/docs/{id}/review/reject/   驳回（理由必填，文件保留）
- POST /api/knowledge/docs/{id}/review/resubmit/ 上传者重新提交（rejected → pending）

权限：审核类接口仅 reviewer；super_admin 不参与审核（超管上传免审），调用即 403。
"""
import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import KnowledgeDoc, KnowledgeDocReview
from .permissions import IsReviewer
from .review import (
    APPROVED,
    PENDING,
    REJECTED,
    dispatch_index_tasks,
    reviewer_can_review,
    reviewer_scope_q,
)
from .serializers import (
    DocReviewRejectSerializer,
    DocReviewResubmitSerializer,
    KnowledgeDocListSerializer,
    KnowledgeDocReviewRecordSerializer,
)

logger = logging.getLogger(__name__)


class PendingReviewListView(generics.ListAPIView):
    """待审列表

    GET /api/knowledge/reviews/pending/
    Query 参数：
        - search: 标题 / 文件名 / 上传者用户名 模糊匹配
        - file_type: 文件类型
        - start_date / end_date: 上传时间范围（YYYY-MM-DD）
    排序：上传时间升序（先传先审）
    """

    permission_classes = [IsAuthenticated, IsReviewer]
    serializer_class = KnowledgeDocListSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = (
            KnowledgeDoc.objects
            .filter(review_status=PENDING)
            .filter(reviewer_scope_q(user))
            .select_related('college', 'discipline', 'uploader', 'reviewer')
        )

        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(file_name__icontains=search)
                | Q(uploader__username__icontains=search)
            )

        file_type = self.request.query_params.get('file_type', '').strip()
        if file_type:
            queryset = queryset.filter(file_type=file_type)

        start_date = self.request.query_params.get('start_date', '').strip()
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)

        end_date = self.request.query_params.get('end_date', '').strip()
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        return queryset.order_by('created_at')


class DocReviewHistoryView(generics.ListAPIView):
    """本人已审记录

    GET /api/knowledge/reviews/history/
    Query 参数：
        - action: approve / reject / resubmit
        - search: 文档标题模糊匹配
    """

    permission_classes = [IsAuthenticated, IsReviewer]
    serializer_class = KnowledgeDocReviewRecordSerializer

    def get_queryset(self):
        queryset = (
            KnowledgeDocReview.objects
            .filter(reviewer=self.request.user)
            .select_related('doc', 'reviewer')
        )
        action = self.request.query_params.get('action', '').strip()
        if action:
            queryset = queryset.filter(action=action)
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(doc__title__icontains=search)
        return queryset.order_by('-created_at')


class _DocReviewBaseView(APIView):
    """审核动作基类：取文档 + 校验审核范围 + 校验当前审核状态。"""

    permission_classes = [IsAuthenticated, IsReviewer]

    def get_doc(self, request, pk):
        doc = get_object_or_404(
            KnowledgeDoc.objects.select_related('uploader', 'college', 'reviewer'),
            id=pk,
        )
        if not reviewer_can_review(request.user, doc):
            raise PermissionDenied('该文档不在你的审核范围内')
        return doc

    def conflict_response(self):
        return Response(
            {'detail': '该文档已被处理，请刷新后重试', 'code': 'REVIEW_CONFLICT'},
            status=status.HTTP_409_CONFLICT,
        )


class DocReviewApproveView(_DocReviewBaseView):
    """审核通过

    POST /api/knowledge/docs/{id}/review/approve/
    通过后派发解析 / 向量化 / 自动关联任务（"先审后索引"）。
    """

    def post(self, request, pk):
        doc = self.get_doc(request, pk)

        updated = (
            KnowledgeDoc.objects
            .filter(id=doc.id, review_status=PENDING)
            .update(
                review_status=APPROVED,
                reviewer=request.user,
                reviewed_at=timezone.now(),
                review_comment='',
            )
        )
        if not updated:
            return self.conflict_response()

        KnowledgeDocReview.objects.create(
            doc_id=doc.id, action='approve', reviewer=request.user,
        )
        dispatch_index_tasks(doc.id)
        logger.info(
            '文档审核通过 doc_id=%s reviewer=%s', doc.id, request.user.id,
        )

        doc.refresh_from_db()
        return Response(KnowledgeDocListSerializer(doc).data)


class DocReviewRejectView(_DocReviewBaseView):
    """审核驳回

    POST /api/knowledge/docs/{id}/review/reject/
    请求体：{"comment": "驳回理由（必填）"}
    驳回后文档保留（OSS 文件不删），不派发索引任务，不会被检索到。
    """

    def post(self, request, pk):
        serializer = DocReviewRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.validated_data['comment'].strip()

        doc = self.get_doc(request, pk)

        updated = (
            KnowledgeDoc.objects
            .filter(id=doc.id, review_status=PENDING)
            .update(
                review_status=REJECTED,
                reviewer=request.user,
                reviewed_at=timezone.now(),
                review_comment=comment,
            )
        )
        if not updated:
            return self.conflict_response()

        KnowledgeDocReview.objects.create(
            doc_id=doc.id, action='reject', reviewer=request.user, comment=comment,
        )
        logger.info(
            '文档审核驳回 doc_id=%s reviewer=%s', doc.id, request.user.id,
        )

        doc.refresh_from_db()
        return Response(KnowledgeDocListSerializer(doc).data)


class DocReviewResubmitView(APIView):
    """被驳回后重新提交审核（上传者本人操作）

    POST /api/knowledge/docs/{id}/review/resubmit/
    请求体：{"comment": "修改说明（可选）"}
    状态 rejected → pending，重新进入对应审核人的待审列表；文件沿用，无需重传。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        doc = get_object_or_404(KnowledgeDoc, id=pk)

        if doc.uploader_id != request.user.id:
            raise PermissionDenied('只有上传者本人可以重新提交')
        if doc.review_status != REJECTED:
            return Response(
                {'detail': '只有被驳回的文档可以重新提交', 'code': 'REVIEW_STATE_INVALID'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = DocReviewResubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.validated_data.get('comment', '').strip()

        updated = (
            KnowledgeDoc.objects
            .filter(id=doc.id, review_status=REJECTED)
            .update(
                review_status=PENDING,
                reviewer=None,
                reviewed_at=None,
                review_comment='',
            )
        )
        if not updated:
            return Response(
                {'detail': '该文档已被处理，请刷新后重试', 'code': 'REVIEW_CONFLICT'},
                status=status.HTTP_409_CONFLICT,
            )

        KnowledgeDocReview.objects.create(
            doc_id=doc.id, action='resubmit', reviewer=request.user, comment=comment,
        )

        doc.refresh_from_db()
        return Response(KnowledgeDocListSerializer(doc).data)
