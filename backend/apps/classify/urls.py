from django.urls import path

from .views import CollegeListView, DisciplineListView, CategoryListView, DepartmentListView


urlpatterns = [
    path('colleges/', CollegeListView.as_view(), name='college_list'),
    path('disciplines/', DisciplineListView.as_view(), name='discipline_list'),
    path('categories/', CategoryListView.as_view(), name='category_list'),
    path('departments/', DepartmentListView.as_view(), name='department_list'),
]
