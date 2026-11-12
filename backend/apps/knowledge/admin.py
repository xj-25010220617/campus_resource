from django.contrib import admin

from .models import CrawlRun


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
