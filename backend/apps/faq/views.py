import logging

from django.db import models
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from config.permissions import IsFAQAdmin, IsSuperAdmin

from .models import FAQCategory, FAQGenerationLog, FAQItem
from .serializers import (
    FAQCategorySerializer,
    FAQGenerationLogSerializer,
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
    获取 FAQ 列表

    规则：
    - 当前用户 college_id 匹配 college（NULL 的全局 FAQ 也可见）
    - 按 frequency 降序排列
    - 每页 10 条

    支持参数：
    - q (str): 关键词搜索
    - status (str): 状态筛选（不传则返回全部），可选 draft/published/rejected
    - category (int): 分类筛选
    - college_id (int): 学院筛选
    - page (int): 页码
    - page_size (int): 每页条数（最大 100）
    """
    serializer_class = FAQItemListSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None  # 前端自行分页

    def get_queryset(self):
        user = self.request.user
        # 超管看全部，其他角色只看本学院+全局
        if user.role == 'super_admin':
            qs = FAQItem.objects.all()
        else:
            q = models.Q(college__isnull=True)
            if user.college_id:
                q |= models.Q(college_id=user.college_id)
            qs = FAQItem.objects.filter(q)

        # 按状态筛选（不传则返回全部状态）
        status_filter = self.request.query_params.get('status', '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        # 搜索功能：支持按问题、答案、标签、分类名称模糊搜索
        search_query = self.request.query_params.get('q', '').strip()
        if search_query:
            # 构建搜索条件
            search_q = models.Q(question__icontains=search_query)
            search_q |= models.Q(answer__icontains=search_query)
            # tags 是 JSONField 数组，使用 contains 匹配数组中的元素
            search_q |= models.Q(tags__contains=search_query)
            # 搜索分类名称
            search_q |= models.Q(category__name__icontains=search_query)

            qs = qs.filter(search_q)

        # 按分类筛选
        category_id = self.request.query_params.get('category', '').strip()
        if category_id:
            qs = qs.filter(category_id=category_id)

        # 按学院筛选
        college_id = self.request.query_params.get('college_id', '').strip()
        if college_id:
            qs = qs.filter(college_id=college_id)

        return qs.order_by('-frequency', '-created_at')


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
    GET /api/faq/manage/items/  (管理端)
    全部 FAQ 列表（含草稿），管理员用

    支持筛选参数：
    - search (str): 关键词搜索（匹配问题/答案/标签）
    - college_id (int): 按学院筛选
    - category (int): 按分类筛选
    - status (str): 按状态筛选（draft / published / rejected）
    """
    serializer_class = FAQItemListSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'super_admin':
            qs = FAQItem.objects.all()
        else:
            q = models.Q(college__isnull=True)
            if user.college_id:
                q |= models.Q(college_id=user.college_id)
            qs = FAQItem.objects.filter(q)

        # 筛选条件
        search = self.request.query_params.get('search')
        college_id = self.request.query_params.get('college_id')
        category = self.request.query_params.get('category')
        status = self.request.query_params.get('status')

        if search:
            from django.db.models import Q as _Q
            qs = qs.filter(
                _Q(question__icontains=search)
                | _Q(answer__icontains=search)
                | _Q(tags__icontains=search)
            )
        if college_id:
            qs = qs.filter(college_id=college_id)
        if category:
            qs = qs.filter(category_id=category)
        if status:
            qs = qs.filter(status=status)

        return qs.order_by('-frequency', '-created_at')


class FAQManageDeleteView(generics.DestroyAPIView):
    """
    删除 FAQ

    DELETE /api/faq/manage/items/{id}/
    功能：物理删除指定 FAQ；非 super_admin 仅能删除本学院或全局 FAQ
    权限：IsFAQAdmin（按 college 范围过滤可见集）
    返回：204 No Content
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


class FAQGenerationLogListView(generics.ListAPIView):
    """
    GET /api/faq/generation-logs/
    FAQ 自动生成日志列表

    权限：IsFAQAdmin（super_admin / college_admin / dept_admin / admin）
    按 started_at 倒序，分页由 settings.DEFAULT_PAGINATION_CLASS 控制
    支持 status 过滤：?status=running|completed|failed
    """
    serializer_class = FAQGenerationLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]

    def get_queryset(self):
        qs = FAQGenerationLog.objects.all().order_by('-started_at')
        status_filter = self.request.query_params.get('status')
        if status_filter in ('running', 'completed', 'failed'):
            qs = qs.filter(status=status_filter)
        return qs


class FAQGenerationLogDetailView(generics.RetrieveAPIView):
    """
    GET /api/faq/generation-logs/{id}/
    单条 FAQ 生成日志详情（含各阶段计数与耗时）。

    配合 POST /api/faq/generate/ 触发后轮询此接口即可获知进度：
    - status=running  → 进行中
    - status=completed → 成功，可查看 drafts_generated
    - status=failed    → 失败，error_message 有堆栈
    """
    queryset = FAQGenerationLog.objects.all()
    serializer_class = FAQGenerationLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsFAQAdmin]
    lookup_field = 'pk'


class FAQGenerateTriggerView(generics.GenericAPIView):
    """
    手动触发 FAQ 自动生成任务

    POST /api/faq/generate/
    功能：立即投递一次 FAQ 生成管道（采集 → 向量聚类 → LLM 草稿）到 Celery 异步执行。
          仅 super_admin 可触发。已有未超时 running 任务时返回 409，避免重复堆积。
    权限：super_admin
    返回：
        - 200 + {log_id, status, task_id, message}：已创建 running 日志并投递任务
        - 200 + {log_id, status, task_id: null}：Celery 不可用时降级同步执行
        - 409 + {detail, code}：已有 FAQ 生成任务正在进行
    """
    permission_classes = [permissions.IsAuthenticated, IsSuperAdmin]

    def post(self, request, *args, **kwargs):
        # 防重叠：与 Celery 任务内部逻辑保持一致——running 且未超 2 小时则拒绝
        from datetime import timedelta

        running_log = FAQGenerationLog.objects.filter(status='running').first()
        if running_log and running_log.started_at \
                and running_log.started_at > timezone.now() - timedelta(hours=2):
            return Response(
                {
                    'detail': f'已有 FAQ 生成任务正在进行（log_id={running_log.id}），请稍后再试',
                    'code': 'FAQ_GENERATION_RUNNING',
                },
                status=status.HTTP_409_CONFLICT,
            )

        log = FAQGenerationLog.objects.create(status='running')
        logger.info('手动触发 FAQ 生成（log_id=%d, operator=%s）', log.id, request.user.username)

        # 遵循 CONVENTIONS §9：视图层不写 try/except，由 safe_delay 捕获并降级
        from utils.celery_dispatcher import safe_delay
        from .tasks import generate_faq_weekly

        async_result = safe_delay(generate_faq_weekly)

        return Response(
            {
                'log_id': log.id,
                'status': log.status,
                'task_id': getattr(async_result, 'id', None),
                'message': 'FAQ 生成任务已投递，可通过 GET /api/faq/generation-logs/{id}/ 查看进度',
            },
            status=status.HTTP_200_OK,
        )
