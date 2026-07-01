from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, College


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'role', 'college', 'email', 'is_active', 'date_joined']
    list_filter = ['role', 'college', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('扩展信息', {'fields': ('role', 'college', 'phone', 'avatar')}),
    )


@admin.register(College)
class CollegeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'sort_order']
    search_fields = ['name']
