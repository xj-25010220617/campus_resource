"""Resources - 上传日志视图"""
from django.db.models import Count, Sum, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from config.permissions import IsAdminOrSuperAdmin
from .models import UploadLog
from .serializers import UploadLogSerializer, UploadLogStatsSerializer


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
