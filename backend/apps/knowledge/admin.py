"""apps.knowledge 的 Django Admin。

Phase 1-2 之前本文件是 2 行占位（`# Register your models here.`），
`ContentModeration` 完全没有后台入口 —— 这正是生产审计报告 P-2 的一半：
`confirmed_unchecked` 既没有写入方，运维也看不到审核状态。

现在本文件只做一件事：**只读地**把 `ContentModeration` 暴露给运维
（列表 / 筛选 / 搜索 / 详情），让"哪些文档停在 rejected / error"可见。

2026-09-28 变更
---------------
`unavailable` 改为自动入库后，"人工确认放行" action 与其 service
（`apps/knowledge/confirmation.py`）已整体删除：既然 `unavailable` 不再需要
人工放行，保留一个能改写审核状态的按钮只会成为一条绕过存储门的路径。
因此本后台**不再有任何写入能力** —— `has_add_permission` /
`has_delete_permission` 一律 False，所有字段进 `readonly_fields`，
也不注册任何 action。审核状态只能由 `moderate_document()` 写入。
"""

from django.contrib import admin

from .models import ContentModeration


@admin.register(ContentModeration)
class ContentModerationAdmin(admin.ModelAdmin):
    """审核记录**只读**视图（无 action、无编辑、无新增、无删除）。"""

    list_display = (
        'id', 'doc_title', 'state', 'storage_state', 'verdict',
        'failure_reason_short', 'checked_at', 'updated_at',
    )
    list_filter = ('state', 'storage_state', 'verdict')
    search_fields = (
        'doc__title', 'doc__file_name', 'quarantine_key', 'promoted_key',
    )
    ordering = ('-updated_at',)
    date_hierarchy = 'created_at'

    # 全部只读：审核状态是安全事实，任何字段级直接改都会绕过
    # moderate_document() 的判定链。
    readonly_fields = tuple(f.name for f in ContentModeration._meta.fields)
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # ------------------------------------------------------------------
    # 列显示辅助
    # ------------------------------------------------------------------
    @admin.display(description='文档标题')
    def doc_title(self, obj):
        return getattr(obj.doc, 'title', '') or '(无标题)'

    @admin.display(description='不可用/异常原因')
    def failure_reason_short(self, obj):
        reason = obj.failure_reason or ''
        if len(reason) <= 64:
            return reason
        return reason[:61] + '…'
