"""Resources - 资源列表与上传日志视图"""
from django.db.models import Count, Sum
from django.db.models.functions import Cast
from django.db.models import TextField
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
    UploadLogCompleteSerializer,
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
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            start = parse_datetime(start_date) if 'T' in start_date else timezone.make_aware(
                __import__('datetime').datetime.strptime(start_date, '%Y-%m-%d'))
            queryset = queryset.filter(created_at__gte=start)
        if end_date:
            end = parse_datetime(end_date) if 'T' in end_date else timezone.make_aware(
                __import__('datetime').datetime.strptime(end_date, '%Y-%m-%d').replace(
                    hour=23, minute=59, second=59))
            queryset = queryset.filter(created_at__lte=end)
        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        college_id = self.request.query_params.get('college_id')
        if college_id:
            queryset = queryset.filter(college_id=college_id)
        return queryset

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """上传统计

        Query 参数:
            - start_date / end_date: 时间范围（ISO 格式或 YYYY-MM-DD）
            - user_id: 上传用户 ID
            - college_id: 学院 ID
            - group_by: 逗号分隔的子集，仅返回指定的分组，
              可选值: status / file_type / college / user / all（默认 all）
        """
        queryset = self.get_queryset()
        group_by_raw = request.query_params.get('group_by', 'all')
        allowed = {'status', 'file_type', 'college', 'user', 'all'}
        group_by = {
            g.strip() for g in group_by_raw.split(',') if g.strip()
        } & allowed
        if not group_by or 'all' in group_by:
            group_by = {'status', 'file_type', 'college', 'user'}

        total_count = queryset.count()
        success_count = queryset.filter(status='success').count()
        failed_count = queryset.filter(status='failed').count()
        total_size = queryset.aggregate(s=Sum('file_size'))['s'] or 0

        data = {
            'total_count': total_count,
            'success_count': success_count,
            'failed_count': failed_count,
            'total_size': total_size,
        }

        if 'status' in group_by:
            data['by_status'] = dict(
                queryset.values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            )
        else:
            data['by_status'] = {}

        if 'file_type' in group_by:
            by_file_type_qs = queryset.values('file_type').annotate(count=Count('id'))
            data['by_file_type'] = {
                item['file_type'] or 'unknown': {
                    'label': item['file_type'] or '未知',
                    'count': item['count'],
                }
                for item in by_file_type_qs
            }
        else:
            data['by_file_type'] = {}

        if 'college' in group_by:
            by_college_qs = list(
                queryset.values('college__id', 'college__name')
                .annotate(count=Count('id'), total_size=Sum('file_size'))
                .order_by('-count')[:20]
            )
            data['by_college'] = [
                {'id': c['college__id'], 'name': c['college__name'],
                 'count': c['count'], 'total_size': c['total_size']}
                for c in by_college_qs
            ]
        else:
            data['by_college'] = []

        if 'user' in group_by:
            by_user_qs = list(
                queryset.values('user__id', 'user__username')
                .annotate(count=Count('id'), total_size=Sum('file_size'))
                .order_by('-count')[:10]
            )
            data['by_user'] = [
                {'id': u['user__id'], 'username': u['user__username'],
                 'count': u['count'], 'total_size': u['total_size']}
                for u in by_user_qs
            ]
        else:
            data['by_user'] = []

        serializer = UploadLogStatsSerializer(data)
        return Response(serializer.data)

    @action(detail=True, methods=['patch'], url_path='complete')
    def complete(self, request, pk=None):
        """更新上传日志状态（内部接口）

        PATCH /api/resources/upload-log/{id}/complete/

        请求体:
            - status: str, 必填，success 或 failed
            - resource_id: int, success 时必填，关联的资源ID
            - error_message: str, failed 时必填，错误信息

        响应:
            - 200: 更新成功
            - 400: 参数校验失败
            - 404: 上传日志不存在
        """
        upload_log = self.get_object()
        serializer = UploadLogCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        upload_log.status = validated_data['status']
        upload_log.completed_at = timezone.now()

        if validated_data['status'] == 'success':
            upload_log.resource_id = validated_data['resource_id']
            upload_log.error_message = ''
        else:
            upload_log.resource = None
            upload_log.error_message = validated_data['error_message']

        upload_log.save()
        return Response(UploadLogSerializer(upload_log).data)
