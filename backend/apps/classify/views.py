from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions

from apps.accounts.models import College
from .models import Discipline, Category
from .serializers import CollegeSerializer, DisciplineSerializer, CategorySerializer


@extend_schema(
    summary='学院列表',
    description='返回全校所有二级学院，按 sort_order 排序',
    tags=['分类管理'],
    operation_id='学院列表',
)
class CollegeListView(generics.ListAPIView):
    """
    学院列表

    GET /api/colleges/
    功能：返回全校所有二级学院，按 sort_order 排序
    返回：学院列表，每条包含 id/name/code/sort_order 字段
    """
    queryset = College.objects.all()
    serializer_class = CollegeSerializer
    permission_classes = [permissions.IsAuthenticated]


@extend_schema(
    summary='学科方向列表',
    description='返回所有学科方向，支持按门类筛选，按 sort_order 排序',
    tags=['分类管理'],
    operation_id='学科方向列表',
)
class DisciplineListView(generics.ListAPIView):
    """
    学科方向列表

    GET /api/disciplines/
    功能：返回所有学科方向，支持按门类筛选，按 sort_order 排序
    参数：
        - category (string, optional): 学科门类（工学/管理学/艺术学/文学/医学）
    返回：学科方向列表，每条包含 id/name/category/sort_order 字段
    """
    serializer_class = DisciplineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Discipline.objects.all()
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        return queryset


@extend_schema(
    summary='资源类型列表',
    description='返回两级树形资源类型结构，一级分类的 parent 为 null',
    tags=['分类管理'],
    operation_id='资源类型列表',
)
class CategoryListView(generics.ListAPIView):
    """
    资源类型列表（树形，含图标）

    GET /api/categories/
    功能：返回两级树形资源类型结构，一级分类的 parent 为 null
    返回：树形列表，每条含 id/name/parent/icon/sort_order/children 字段，
          icon 字段支持二级分类继承父级图标
    """
    queryset = Category.objects.filter(parent__isnull=True).prefetch_related('children')
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
