from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    AcademicCalendar, AdmissionRule, College, SchoolProfile,
    Scholarship, Tuition, User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'role', 'college', 'email', 'is_active', 'date_joined']
    list_filter = ['role', 'college', 'is_active']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('扩展信息', {'fields': ('role', 'college', 'phone', 'avatar')}),
    )


@admin.register(College)
class CollegeAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'code', 'dean', 'sort_order']
    list_filter = ['code']
    search_fields = ['name', 'code', 'intro']


@admin.register(SchoolProfile)
class SchoolProfileAdmin(admin.ModelAdmin):
    """单例：后台永远只显示一条记录，编辑即可"""
    list_display = ['name_full', 'motto', 'president', 'student_count', 'teacher_count', 'updated_at']

    def has_add_permission(self, request):
        return False   # 禁止新增，只能编辑唯一那条

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Scholarship)
class ScholarshipAdmin(admin.ModelAdmin):
    list_display = ['name', 'level', 'type', 'amount', 'sort_order']
    list_filter = ['level', 'type']
    search_fields = ['name']


@admin.register(Tuition)
class TuitionAdmin(admin.ModelAdmin):
    list_display = ['category', 'amount_text']
    search_fields = ['category']


@admin.register(AdmissionRule)
class AdmissionRuleAdmin(admin.ModelAdmin):
    list_display = ['year', 'content']
    ordering = ['-year']


@admin.register(AcademicCalendar)
class AcademicCalendarAdmin(admin.ModelAdmin):
    list_display = ['term_name', 'term_type', 'start_date', 'end_date', 'sort_order']
    list_filter = ['term_type']

