from django.urls import path

from .views import (
    CategoryListView, CollegeListView, DepartmentListView,
    DisciplineListView, FirstLevelCategoryListView, SecondLevelCategoryListView,
)


urlpatterns = [
    path('colleges/', CollegeListView.as_view(), name='college_list'),
    path('disciplines/', DisciplineListView.as_view(), name='discipline_list'),
    path('categories/', CategoryListView.as_view(), name='category_list'),
    path('categories/first-level/', FirstLevelCategoryListView.as_view(), name='category_first_level'),
    path('categories/second-level/', SecondLevelCategoryListView.as_view(), name='category_second_level'),
    path('departments/', DepartmentListView.as_view(), name='department_list'),
]
