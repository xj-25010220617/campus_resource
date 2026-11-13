from django.contrib import admin

from .models import CrawlRun, TabularFewShot


@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    """官网采集记录（只读，用于观测持续更新是否推进）"""

    list_display = (
        'started_at', 'trigger', 'status', 'pages_fetched',
        'docs_saved', 'files_saved', 'skipped_saved',
        'duration_sec', 'message',
    )
    list_filter = ('status', 'trigger')
    date_hierarchy = 'started_at'
    ordering = ('-started_at',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(TabularFewShot)
class TabularFewShotAdmin(admin.ModelAdmin):
    """表格问答示例：勾选 verified 后才会注入 SQL 生成 Prompt"""

    list_display = ('question', 'doc_title', 'verified', 'updated_at')
    list_editable = ('verified',)
    list_filter = ('verified',)
    search_fields = ('question', 'sql', 'doc_title')
    readonly_fields = ('question_hash', 'embedding', 'created_at', 'updated_at')
    ordering = ('-updated_at',)
