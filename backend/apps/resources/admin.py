from django.contrib import admin
from .models import Resource, Favorite


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'file_type', 'college', 'discipline', 'uploader', 'created_at']
    list_filter = ['status', 'file_type', 'college', 'discipline']
    search_fields = ['title', 'description', 'file_name']
    date_hierarchy = 'created_at'


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ['user', 'resource', 'created_at']
    list_filter = ['created_at']
