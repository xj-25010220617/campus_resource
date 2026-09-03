from django.contrib import admin

from .models import UploadLog


@admin.register(UploadLog)
class UploadLogAdmin(admin.ModelAdmin):
    list_display = ['file_name', 'status', 'file_type', 'college', 'user', 'created_at']
    list_filter = ['status', 'file_type', 'college']
    search_fields = ['file_name', 'user__username']
    date_hierarchy = 'created_at'
