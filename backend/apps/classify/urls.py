from django.urls import path

from .views import CollegeListView, DisciplineListView, CategoryListView


urlpatterns = [
    path('colleges/', CollegeListView.as_view(), name='college_list'),
    path('disciplines/', DisciplineListView.as_view(), name='discipline_list'),
    path('categories/', CategoryListView.as_view(), name='category_list'),
]
