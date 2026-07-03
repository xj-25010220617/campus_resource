from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions

from apps.accounts.models import College
from .models import Discipline, Category, Department
from .serializers import (
    CategorySerializer, CollegeSerializer, DepartmentSerializer,
    DisciplineSerializer, FirstLevelCategorySerializer, SecondLevelCategorySerializer,
)


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


@extend_schema(
    summary='部门列表',
    description='返回两级树形部门结构，一级部门的 parent 为 null',
    tags=['分类管理'],
    operation_id='部门列表',
)
class DepartmentListView(generics.ListAPIView):
    """
    部门列表（树形结构）

    GET /api/departments/
    功能：返回两级树形部门结构，一级部门的 parent 为 null
    返回：树形列表，每条含 id/name/parent/sort_order/children 字段
    """
    queryset = Department.objects.filter(parent__isnull=True).prefetch_related('children')
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]


class FirstLevelCategoryListView(generics.ListAPIView):
    """
    一级分类列表（不含子级，供下拉框使用）

    GET /api/categories/first-level/
    功能：返回所有一级分类，按 sort_order 排序，供前端第一个下拉框使用
    返回：[{id, name, icon}, ...]
    """
    queryset = Category.objects.filter(parent__isnull=True)
    serializer_class = FirstLevelCategorySerializer
    permission_classes = [permissions.IsAuthenticated]


class SecondLevelCategoryListView(generics.ListAPIView):
    """
    二级分类列表（按父级筛选，供联动下拉框使用）

    GET /api/categories/second-level/?parent_id=<id>
    功能：选择一级分类后，返回该一级分类下的所有二级分类
    参数：
        - parent_id (int, required): 一级分类 ID
    返回：[{id, name}, ...]
    """
    serializer_class = SecondLevelCategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        parent_id = self.request.query_params.get('parent_id')
        if not parent_id:
            return Category.objects.none()
        return Category.objects.filter(parent_id=parent_id)
