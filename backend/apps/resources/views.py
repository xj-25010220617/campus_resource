"""Resources - 资源列表与上传日志视图"""
import datetime as _datetime

from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from drf_spectacular.utils import extend_schema

from apps.admin_panel.permissions import IsAnyAdmin, scope_upload_logs

from .models import UploadLog
from .serializers import (
    UploadLogSerializer,
    UploadLogStatsSerializer,
    UploadLogCompleteSerializer,
)


@extend_schema(
    summary='上传日志',
    description='上传日志管理，支持查看和统计功能',
    tags=['资源管理'],
)
class UploadLogViewSet(viewsets.ReadOnlyModelViewSet):
    """上传日志 ViewSet（只读）

    GET /api/resources/upload-logs/
    功能：分页返回上传日志列表，支持按状态/文件类型/学院/部门/用户/时间筛选
    Query 参数：
        - start_date / end_date: 时间范围（ISO 或 YYYY-MM-DD）
        - user_id: 上传用户 ID
        - college_id: 学院 ID
        - department_id: 部门 ID
        - status: 上传状态
        - file_type: 文件类型
    权限：
        - super_admin：平台全部
        - college_admin / admin：本学院日志
        - dept_admin：本部门日志
        - 其他：无权限
    """
    queryset = UploadLog.objects.select_related(
        'user', 'college', 'department', 'knowledge_doc',
    )
    serializer_class = UploadLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsAnyAdmin]
    filterset_fields = ['status', 'file_type', 'college', 'department']
    search_fields = ['file_name', 'user__username']
    ordering_fields = ['created_at', 'file_size']

    def get_queryset(self):
        queryset = super().get_queryset()
        # 范围过滤（按当前用户角色限定可见日志范围）
        queryset = scope_upload_logs(queryset, self.request.user)

        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            start = (
                parse_datetime(start_date) if 'T' in start_date
                else timezone.make_aware(
                    _datetime.datetime.strptime(start_date, '%Y-%m-%d')
                )
            )
            queryset = queryset.filter(created_at__gte=start)
        if end_date:
            end = (
                parse_datetime(end_date) if 'T' in end_date
                else timezone.make_aware(
                    _datetime.datetime.strptime(end_date, '%Y-%m-%d').replace(
                        hour=23, minute=59, second=59,
                    )
                )
            )
            queryset = queryset.filter(created_at__lte=end)
        user_id = self.request.query_params.get('user_id')
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        college_id = self.request.query_params.get('college_id')
        if college_id:
            queryset = queryset.filter(college_id=college_id)
        department_id = self.request.query_params.get('department_id')
        if department_id:
            queryset = queryset.filter(department_id=department_id)
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
            - knowledge_doc_id: int, success 时必填，关联的知识文档ID
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
            upload_log.knowledge_doc_id = validated_data['knowledge_doc_id']
            upload_log.error_message = ''
        else:
            upload_log.knowledge_doc = None
            upload_log.error_message = validated_data['error_message']

        upload_log.save()
        return Response(UploadLogSerializer(upload_log).data)
