"""apps.knowledge 的 Django Admin。

Phase 1-2 之前本文件是 2 行占位（`# Register your models here.`），
`ContentModeration` 完全没有后台入口 —— 这正是生产审计报告 P-2 的一半：
`confirmed_unchecked` 既没有写入方，运维也看不到审核状态。

本文件只做两件事：

1. **只读地**把 `ContentModeration` 暴露给运维（列表 / 筛选 / 搜索 / 详情），
   让"哪些文档卡在 unavailable"可见。
2. 提供唯一的人工放行入口 `confirm_manually`，它**不自己写库**，只收集理由
   然后调用 `apps.knowledge.confirmation.confirm_unchecked_document()`。

刻意**不**开放编辑：审核状态是安全事实，任何字段级直接改都会绕过
`confirmation` 的权限 / 状态 / 对象存在性校验。因此 `has_add_permission`
与 `has_delete_permission` 一律 False，所有字段进 `readonly_fields`。
"""

from django.contrib import admin, messages
from django.shortcuts import render
from django.utils.html import format_html

from .confirmation import ConfirmationError, confirm_unchecked_document
from .models import ContentModeration

# 确认页收集理由的 POST 字段名。用它的存在与否区分"请求确认页"和"已提交理由"。
REASON_FIELD = 'confirmation_reason'


@admin.register(ContentModeration)
class ContentModerationAdmin(admin.ModelAdmin):
    """审核记录只读视图 + 人工放行 action。"""

    list_display = (
        'id', 'doc_title', 'state', 'storage_state', 'verdict',
        'quarantine_key_short', 'confirmed_at', 'updated_at',
    )
    list_filter = ('state', 'storage_state', 'verdict')
    search_fields = (
        'doc__title', 'doc__file_name', 'quarantine_key', 'promoted_key',
    )
    ordering = ('-updated_at',)
    actions = ('confirm_manually',)

    # 全部只读：状态只能经由 confirmation.confirm_unchecked_document() 变更
    readonly_fields = tuple(
        f.name for f in ContentModeration._meta.fields
    )
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

    @admin.display(description='隔离区 Key')
    def quarantine_key_short(self, obj):
        key = obj.quarantine_key or ''
        if len(key) <= 48:
            return key
        return format_html('<span title="{}">{}</span>', key, key[:45] + '…')

    # ------------------------------------------------------------------
    # 人工确认放行
    # ------------------------------------------------------------------
    @admin.action(description='人工确认放行（仅 unavailable，需填写理由）')
    def confirm_manually(self, request, queryset):
        """两步式 action：先收集理由，再逐条调用 service。

        本 action **不做任何业务判断**——能不能放行由
        `confirmation.confirm_unchecked_document()` 决定，这里只负责
        收集输入、逐条调用、把每条的失败原因回报给操作者。
        """
        # 第二步：已提交理由
        if REASON_FIELD in request.POST:
            reason = (request.POST.get(REASON_FIELD) or '').strip()
            if not reason:
                self.message_user(
                    request, '必须填写确认理由，未执行任何确认。', messages.ERROR,
                )
                return None

            ok, failed = 0, []
            for moderation in queryset.select_related('doc'):
                try:
                    confirm_unchecked_document(
                        moderation.doc,
                        actor=request.user,
                        reason=reason,
                        worker_id=f'admin:{getattr(request.user, "pk", "")}',
                    )
                    ok += 1
                except ConfirmationError as e:
                    failed.append(f'#{moderation.pk} {moderation.doc_id}: {e.detail}')
                except Exception as e:  # 不可预期的错误也要逐条隔离，不中断其余
                    failed.append(f'#{moderation.pk} {moderation.doc_id}: {type(e).__name__}: {e}')

            if ok:
                self.message_user(request, f'已确认放行 {ok} 份文档。', messages.SUCCESS)
            for line in failed:
                self.message_user(request, f'未放行 — {line}', messages.ERROR)
            return None

        # 第一步：渲染确认页
        return render(request, 'admin/knowledge/confirm_selected.html', {
            'title': '人工确认放行',
            'queryset': queryset,
            'reason_field': REASON_FIELD,
            'opts': self.model._meta,
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
            **self.admin_site.each_context(request),
        })
