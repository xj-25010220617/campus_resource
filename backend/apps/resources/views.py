"""Resources - 资源列表与上传日志视图"""
from django.db.models import Count, Sum, Q
from django.db.models.functions import Cast
from django.db.models import TextField
from django.utils import timezone
from rest_framework import generics, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.classify.models import Category
from config.permissions import IsAdminOrSuperAdmin
from .models import Resource, UploadLog
from .serializers import (
    ResourceListSerializer,
    UploadLogSerializer,
    UploadLogStatsSerializer,
)


class ResourceListView(generics.ListAPIView):
    """
    资源列表接口

    GET /api/resources/
    功能：分页返回已发布资源列表，支持按学院、学科、分类、关键词筛选
    请求参数：
        - page (int, default=1): 页码
        - page_size (int, default=20): 每页数量
        - college_id (int, optional): 按学院ID精确筛选
        - discipline_id (int, optional): 按学科方向ID精确筛选
        - category_id (int, optional): 按资源类型筛选，一级分类自动包含其下所有二级分类
        - keyword (str, optional): 按关键词模糊匹配
    权限规则：所有登录用户均可查看资源列表
    返回：DRF 分页包装后的资源列表，每条包含 id / title / description / file_type / file_size /
          uploader_name / college_name / discipline_name / category_name（完整路径）/
          category_icon / keywords / status / visibility / view_count / download_count / created_at
    """
    serializer_class = ResourceListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Resource.objects.select_related(
            'college', 'discipline', 'category', 'uploader',
        ).all()

        # 学院筛选
        college_id = self.request.query_params.get('college_id')
        if college_id:
            queryset = queryset.filter(college_id=college_id)

        # 学科方向筛选
        discipline_id = self.request.query_params.get('discipline_id')
        if discipline_id:
            queryset = queryset.filter(discipline_id=discipline_id)

        # 分类筛选：一级分类 → 包含其下所有二级分类
        category_id = self.request.query_params.get('category_id')
        if category_id:
            try:
                category = Category.objects.get(id=category_id)
            except Category.DoesNotExist:
                return queryset.none()
            if category.parent is None:
                # 一级分类：获取所有子分类 ID
                child_ids = category.children.values_list('id', flat=True)
                queryset = queryset.filter(category_id__in=child_ids)
            else:
                # 二级分类：精确匹配
                queryset = queryset.filter(category_id=category_id)

        # 关键词模糊匹配（JSONField 文本搜索）
        keyword = self.request.query_params.get('keyword')
        if keyword:
            queryset = queryset.annotate(
                keywords_text=Cast('keywords', TextField()),
            ).filter(keywords_text__icontains=keyword)

        return queryset.order_by('-created_at')


class UploadLogViewSet(viewsets.ReadOnlyModelViewSet):
    """上传日志 ViewSet（只读）"""
    queryset = UploadLog.objects.select_related('user', 'college', 'resource')
    serializer_class = UploadLogSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    filterset_fields = ['status', 'file_type', 'college']
    search_fields = ['file_name', 'user__username']
    ordering_fields = ['created_at', 'file_size']

    def get_queryset(self):
        queryset = super().get_queryset()
        # 按时间范围过滤
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
        # 按用户过滤
        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        return queryset

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """上传统计"""
        queryset = self.get_queryset()

        # 基础统计
        total_count = queryset.count()
        success_count = queryset.filter(status='success').count()
        failed_count = queryset.filter(status='failed').count()
        total_size = queryset.aggregate(s=Sum('file_size'))['s'] or 0

        # 按状态统计
        by_status = dict(
            queryset.values('status')
            .annotate(count=Count('id'))
            .values_list('status', 'count')
        )

        # 按文件类型统计
        by_file_type = {}
        for ft, label in UploadLog.FILE_TYPE_CHOICES:
            count = queryset.filter(file_type=ft).count()
            if count > 0:
                by_file_type[ft] = {'label': label, 'count': count}

        # 按学院统计
        by_college = list(
            queryset.values('college__id', 'college__name')
            .annotate(count=Count('id'), total_size=Sum('file_size'))
            .order_by('-count')[:20]
        )
        by_college = [
            {'id': c['college__id'], 'name': c['college__name'],
             'count': c['count'], 'total_size': c['total_size']}
            for c in by_college
        ]

        # 按用户统计（TOP 10）
        by_user = list(
            queryset.values('user__id', 'user__username')
            .annotate(count=Count('id'), total_size=Sum('file_size'))
            .order_by('-count')[:10]
        )
        by_user = [
            {'id': u['user__id'], 'username': u['user__username'],
             'count': u['count'], 'total_size': u['total_size']}
            for u in by_user
        ]

        data = {
            'total_count': total_count,
            'success_count': success_count,
            'failed_count': failed_count,
            'total_size': total_size,
            'by_status': by_status,
            'by_file_type': by_file_type,
            'by_college': by_college,
            'by_user': by_user,
        }
        serializer = UploadLogStatsSerializer(data)
        return Response(serializer.data)
