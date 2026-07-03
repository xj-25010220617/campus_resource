import logging

from django.db import models
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from config.permissions import IsFAQAdmin, IsSuperAdmin

from .models import FAQCategory, FAQItem
from .serializers import (
    FAQCategorySerializer,
    FAQItemListSerializer,
    FAQItemDetailSerializer,
    FAQItemDraftSerializer,
    FAQItemPublishSerializer,
)

logger = logging.getLogger(__name__)


# ======================================================================
# 公共 API（供前端用户页面调用）
# ======================================================================

class FAQCategoryListView(generics.ListAPIView):
    """
    GET /api/faq/categories/
    获取 FAQ 分类列表
    """
    queryset = FAQCategory.objects.all()
    serializer_class = FAQCategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None


class FAQItemListView(generics.ListAPIView):
    """
    GET /api/faq/items/
    获取已发布的 FAQ 列表

    规则：
    - 仅返回 status='published' 的记录
    - 当前用户 college_id 匹配 college（NULL 的全局 FAQ 也可见）
    - 按 frequency 降序排列
    """
    serializer_class = FAQItemListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return FAQItem.objects.filter(
            status='published',
        ).filter(q).order_by('-frequency', '-created_at')


class FAQItemDetailView(generics.RetrieveAPIView):
    """
    GET /api/faq/items/{id}/
    FAQ 详情
    """
    serializer_class = FAQItemDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return FAQItem.objects.filter(
            status='published',
        ).filter(q)


# ======================================================================
# 管理 API（供管理员后台调用）
# ======================================================================

class FAQDraftListView(generics.ListAPIView):
    """
    GET /api/faq/drafts/
    待审核草稿列表

    权限：
    - super_admin: 全部
    - college_admin: 本学院 + 全局
    """
    serializer_class = FAQItemListSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = FAQItem.objects.filter(status='draft')

        if user.role == 'super_admin':
            return qs.order_by('-frequency', '-created_at')

        # college_admin / dept_admin / admin: 只看本学院的和全局的
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return qs.filter(q).order_by('-frequency', '-created_at')


class FAQDraftDetailView(generics.RetrieveUpdateAPIView):
    """
    GET/PATCH /api/faq/drafts/{id}/
    草稿详情 / 编辑草稿
    """
    serializer_class = FAQItemDraftSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = FAQItem.objects.filter(status='draft')
        if user.role == 'super_admin':
            return qs
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return qs.filter(q)

    def perform_update(self, serializer):
        serializer.save()


class FAQDraftActionView(generics.GenericAPIView):
    """
    POST /api/faq/drafts/{id}/publish/  — 发布
    POST /api/faq/drafts/{id}/reject/  — 驳回
    """
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]
    serializer_class = FAQItemPublishSerializer

    def get_queryset(self):
        user = self.request.user
        qs = FAQItem.objects.filter(status='draft')
        if user.role == 'super_admin':
            return qs
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return qs.filter(q)

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        faq = self.get_object()
        action = serializer.validated_data['action']

        if action == 'publish':
            faq.status = 'published'
        else:
            faq.status = 'rejected'

        faq.reviewed_by = request.user
        faq.reviewed_at = timezone.now()
        faq.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])

        logger.info(
            'FAQ %s: id=%d by user=%s', action, faq.id, request.user.username,
        )

        return Response(
            {'status': 'ok', 'faq_id': faq.id, 'new_status': faq.status},
        )


class FAQManageListView(generics.ListAPIView):
    """
    GET /api/faq/items/  (管理端)
    全部 FAQ 列表（含草稿），管理员用
    """
    serializer_class = FAQItemListSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'super_admin':
            return FAQItem.objects.all().order_by('-frequency', '-created_at')

        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return FAQItem.objects.filter(q).order_by('-frequency', '-created_at')


class FAQManageDeleteView(generics.DestroyAPIView):
    """
    DELETE /api/faq/items/{id}/
    删除 FAQ
    """
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'super_admin':
            return FAQItem.objects.all()
        q = models.Q(college__isnull=True)
        if user.college_id:
            q |= models.Q(college_id=user.college_id)
        return FAQItem.objects.filter(q)
