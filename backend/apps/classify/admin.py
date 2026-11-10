from django.contrib import admin
from .models import Discipline, Category


@admin.register(Discipline)
class DisciplineAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'category', 'college', 'level', 'major_code', 'is_first_class', 'sort_order']
    list_filter = ['category', 'college', 'level', 'is_first_class']
    search_fields = ['name', 'major_code', 'feature_major', 'core_courses']


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'sort_order']
    list_filter = ['parent']
