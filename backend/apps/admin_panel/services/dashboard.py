"""Admin Panel - Dashboard 聚合计算

5 个纯函数对应 5 类日志块，每个函数：
- 接收一个时间区间（start, end）
- 内部 try/except + logger.error，失败返回 `{'error': str}`
- 返回 dict 结构稳定，前端可直接消费

调用方（LogsDashboardView）只负责组装 5 个块的时间区间和循环调用，
不接 try/except（遵循规范 §9「视图不写 try/except」）。
"""
import logging
from datetime import datetime

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import ExtractHour
from django.utils import timezone

from apps.chat.models import QueryLog, SensitiveLog
from apps.resources.models import UploadLog

from ..models import LoginLog, OperationLog


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 时间区间工具
# ----------------------------------------------------------------------
def parse_period(period: str | None):
    """根据 period 计算 (start, end)，时区 Asia/Shanghai。

    - day: 今天 0:00 ~ now
    - week: 本周一 0:00 ~ now
    - month: 本月 1 日 0:00 ~ now
    - 其他/缺省: day
    """
    now = timezone.now()
    if period == 'week':
        start = now - timezone.timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _format_dt(dt: datetime) -> str:
    """统一输出 ISO + 带时区"""
    return dt.isoformat()


# ----------------------------------------------------------------------
# 块 1：上传日志
# ----------------------------------------------------------------------
def compute_upload_block(start: datetime, end: datetime) -> dict:
    """上传日志统计块。"""
    try:
        qs = UploadLog.objects.filter(created_at__range=(start, end))
        total = qs.count()
        by_status = [
            {
                'key': row['status'] or 'unknown',
                'label': row['status'] or '未知',
                'count': row['count'],
            }
            for row in qs.values('status').annotate(count=Count('id'))
        ]
        by_file_type = [
            {
                'key': row['file_type'] or 'unknown',
                'label': row['file_type'] or '未知',
                'count': row['count'],
            }
            for row in qs.values('file_type').annotate(count=Count('id'))
        ]
        by_college_rows = (
            qs.values('college__id', 'college__name')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        by_college = [
            {
                'key': str(row['college__id']) if row['college__id'] else 'none',
                'label': row['college__name'] or '未分类',
                'count': row['count'],
            }
            for row in by_college_rows
        ]
        total_size = qs.aggregate(s=Sum('file_size'))['s'] or 0
        return {
            'total': total,
            'total_size': total_size,
            'by_status': by_status,
            'by_file_type': by_file_type,
            'by_college': by_college,
        }
    except Exception as e:
        logger.error('upload block 计算失败: %s', e)
        return {'error': str(e)}


# ----------------------------------------------------------------------
# 块 2：查询日志
# ----------------------------------------------------------------------
def compute_query_block(start: datetime, end: datetime) -> dict:
    """查询日志统计块。"""
    try:
        qs = QueryLog.objects.filter(created_at__range=(start, end))
        total = qs.count()
        agg = qs.aggregate(
            avg_hit=Avg('hit_count'),
            avg_ms=Avg('response_ms'),
            likes=Count('id', filter=Q(user_feedback=1)),
        )
        like_rate = (agg['likes'] / total) if total else 0
        by_feedback_rows = (
            qs.values('user_feedback').annotate(count=Count('id')).order_by('user_feedback')
        )
        feedback_label_map = {1: '赞', 0: '踩', -1: '无反馈'}
        by_feedback = [
            {
                'key': str(row['user_feedback']),
                'label': feedback_label_map.get(row['user_feedback'], '未知'),
                'count': row['count'],
            }
            for row in by_feedback_rows
        ]
        return {
            'total': total,
            'avg_hit_count': round(agg['avg_hit'] or 0, 2),
            'avg_response_ms': int(agg['avg_ms'] or 0),
            'like_rate': round(like_rate, 4),
            'by_feedback': by_feedback,
        }
    except Exception as e:
        logger.error('query block 计算失败: %s', e)
        return {'error': str(e)}


# ----------------------------------------------------------------------
# 块 3：敏感内容日志
# ----------------------------------------------------------------------
def compute_sensitive_block(start: datetime, end: datetime) -> dict:
    """敏感内容日志统计块。"""
    try:
        qs = SensitiveLog.objects.filter(created_at__range=(start, end))
        return {'total': qs.count()}
    except Exception as e:
        logger.error('sensitive block 计算失败: %s', e)
        return {'error': str(e)}


# ----------------------------------------------------------------------
# 块 4：登录日志
# ----------------------------------------------------------------------
def compute_login_block(start: datetime, end: datetime) -> dict:
    """登录日志统计块。"""
    try:
        qs = LoginLog.objects.filter(created_at__range=(start, end))
        total = qs.count()
        success_count = qs.filter(success=True).count()
        fail_count = qs.filter(success=False).count()
        by_success = [
            {'key': 'true' if row['success'] else 'false',
             'label': '成功' if row['success'] else '失败',
             'count': row['count']}
            for row in qs.values('success').annotate(count=Count('id'))
        ]
        type_label_map = {
            'jwt': '账号密码',
            'sso': 'SSO',
            'sso_mock': 'SSO Mock',
        }
        by_type = [
            {
                'key': row['login_type'] or 'unknown',
                'label': type_label_map.get(row['login_type'], row['login_type'] or '未知'),
                'count': row['count'],
            }
            for row in qs.values('login_type').annotate(count=Count('id'))
        ]
        # 按 24h 小时分桶
        by_hour_rows = (
            qs.annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )
        by_hour = [
            {'key': str(row['hour']), 'label': f'{row["hour"]:02d}:00', 'count': row['count']}
            for row in by_hour_rows
        ]
        return {
            'total': total,
            'success_count': success_count,
            'fail_count': fail_count,
            'by_success': by_success,
            'by_type': by_type,
            'by_hour': by_hour,
        }
    except Exception as e:
        logger.error('login block 计算失败: %s', e)
        return {'error': str(e)}


# ----------------------------------------------------------------------
# 块 5：操作日志
# ----------------------------------------------------------------------
def compute_operation_block(start: datetime, end: datetime) -> dict:
    """操作日志统计块。"""
    try:
        qs = OperationLog.objects.filter(created_at__range=(start, end))
        total = qs.count()
        action_label_map = {
            'create': '新建',
            'update': '编辑',
            'delete': '删除',
            'reset_password': '重置密码',
            'batch_delete': '批量删除',
            'batch_reset_password': '批量重置密码',
        }
        by_action = [
            {
                'key': row['action'],
                'label': action_label_map.get(row['action'], row['action']),
                'count': row['count'],
            }
            for row in qs.values('action').annotate(count=Count('id'))
        ]
        target_label_map = {'user': '用户', 'resource': '资源', 'category': '分类'}
        by_target_type = [
            {
                'key': row['target_type'],
                'label': target_label_map.get(row['target_type'], row['target_type']),
                'count': row['count'],
            }
            for row in qs.values('target_type').annotate(count=Count('id'))
        ]
        top_operators_rows = (
            qs.values('operator_id', 'operator_username')
            .annotate(count=Count('id'))
            .order_by('-count')[:10]
        )
        top_operators = [
            {
                'key': str(row['operator_id']) if row['operator_id'] else 'anonymous',
                'label': row['operator_username'] or '匿名',
                'count': row['count'],
            }
            for row in top_operators_rows
        ]
        return {
            'total': total,
            'by_action': by_action,
            'by_target_type': by_target_type,
            'top_operators': top_operators,
        }
    except Exception as e:
        logger.error('operation block 计算失败: %s', e)
        return {'error': str(e)}
