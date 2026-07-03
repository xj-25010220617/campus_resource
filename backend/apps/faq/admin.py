from django.contrib import admin

from .models import FAQCategory, FAQItem, FAQGenerationLog


@admin.register(FAQCategory)
class FAQCategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'sort_order', 'created_at']
    search_fields = ['name']
    list_editable = ['sort_order']


@admin.register(FAQItem)
class FAQItemAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'question_short', 'category', 'college', 'frequency',
        'variant_count', 'status', 'reviewed_by', 'created_at',
    ]
    list_filter = ['status', 'category', 'college', 'created_at']
    search_fields = ['question', 'answer', 'tags']
    readonly_fields = [
        'frequency', 'variant_count', 'source_period_start', 'source_period_end',
        'related_doc_ids', 'reviewed_by', 'reviewed_at', 'created_at', 'updated_at',
    ]
    actions = ['publish_selected', 'reject_selected']

    def question_short(self, obj):
        return obj.question[:40]
    question_short.short_description = '问题'

    def publish_selected(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(status='draft').update(
            status='published',
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f'已发布 {updated} 条 FAQ')
    publish_selected.short_description = '发布选中草稿'

    def reject_selected(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(status='draft').update(
            status='rejected',
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f'已驳回 {updated} 条 FAQ')
    reject_selected.short_description = '驳回选中草稿'


@admin.register(FAQGenerationLog)
class FAQGenerationLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'status', 'total_questions', 'clusters_found',
        'drafts_generated', 'started_at', 'completed_at',
    ]
    list_filter = ['status', 'started_at']
    readonly_fields = [
        'status', 'total_questions', 'clusters_found',
        'drafts_generated', 'error_message', 'started_at', 'completed_at',
    ]
