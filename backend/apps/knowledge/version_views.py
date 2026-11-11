"""文档新旧版本候选接口。

- GET  /api/knowledge/version-candidates/              待确认的疑似版本候选（上传者本人；超管看全部）
- POST /api/knowledge/version-candidates/{id}/confirm/ 确认替代（旧版随后在问答检索中被隔离）
- POST /api/knowledge/version-candidates/{id}/reject/  拒绝替代（两篇文档保持独立）

权限：只有新文档上传者本人或超级管理员可以确认/拒绝。
"""
import logging

from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocVersionCandidate
from .serializers import DocVersionCandidateSerializer
from .versioning import (
    VersionCandidateNotFound,
    confirm_candidate,
    reject_candidate,
)

logger = logging.getLogger(__name__)


class VersionCandidateListView(generics.ListAPIView):
    """疑似新旧版本候选列表。

    GET /api/knowledge/version-candidates/
    Query 参数：
        - status: pending（默认）/ confirmed / rejected / all
    排序：创建时间倒序（最新上传产生的候选优先展示）
    """
    permission_classes = [IsAuthenticated]
    serializer_class = DocVersionCandidateSerializer

    def get_queryset(self):
        user = self.request.user
        status_filter = self.request.query_params.get('status', 'pending')
        queryset = DocVersionCandidate.objects.select_related(
            'new_doc', 'old_doc',
        ).all()
        if status_filter != 'all':
            queryset = queryset.filter(status=status_filter)
        if getattr(user, 'role', None) != 'super_admin':
            queryset = queryset.filter(new_doc__uploader=user)
        return queryset.order_by('-created_at')


class VersionCandidateConfirmView(APIView):
    """确认版本替代：把旧版标记为历史版本，问答检索从此不再召回旧版。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            candidate = confirm_candidate(pk, request.user)
        except VersionCandidateNotFound:
            raise NotFound('版本候选不存在')
        return Response(DocVersionCandidateSerializer(candidate).data, status=status.HTTP_200_OK)


class VersionCandidateRejectView(APIView):
    """拒绝版本替代：保留两篇独立文档。"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            candidate = reject_candidate(pk, request.user)
        except VersionCandidateNotFound:
            raise NotFound('版本候选不存在')
        return Response(DocVersionCandidateSerializer(candidate).data, status=status.HTTP_200_OK)
