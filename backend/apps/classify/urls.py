from django.urls import path

from .views import (
    CategoryListView, CollegeListView, DepartmentListView,
    DisciplineListView, FirstLevelCategoryListView, SecondLevelCategoryListView,
)


urlpatterns = [
    path('colleges/', CollegeListView.as_view(), name='college_list'),
    path('disciplines/', DisciplineListView.as_view(), name='discipline_list'),
    # 分类接口已废弃（资源分类系统已移除），保留路由以防前端依赖
    path('categories/', CategoryListView.as_view(), name='category_list'),
    path('categories/first-level/', FirstLevelCategoryListView.as_view(), name='category_first_level'),
    path('categories/second-level/', SecondLevelCategoryListView.as_view(), name='category_second_level'),
    path('departments/', DepartmentListView.as_view(), name='department_list'),
]
