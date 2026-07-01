from rest_framework import generics, permissions
from .models import Discipline, Category
from .serializers import DisciplineSerializer, CategorySerializer


class DisciplineListView(generics.ListAPIView):
    """学科方向列表（公开读取）"""
    queryset = Discipline.objects.all()
    serializer_class = DisciplineSerializer
    permission_classes = [permissions.IsAuthenticated]


class CategoryListView(generics.ListAPIView):
    """资源类型列表（树形，公开读取）"""
    queryset = Category.objects.filter(parent__isnull=True).prefetch_related('children')
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
