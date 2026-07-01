from django.urls import path
from .views import DisciplineListView, CategoryListView

urlpatterns = [
    path('disciplines/', DisciplineListView.as_view(), name='discipline_list'),
    path('categories/', CategoryListView.as_view(), name='category_list'),
    path('colleges/', DisciplineListView.as_view(), name='college_list'),  # 学院列表见 accounts
]
