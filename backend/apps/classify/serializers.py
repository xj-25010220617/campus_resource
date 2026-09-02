from rest_framework import serializers

from apps.accounts.models import College
from .models import Discipline, Category, Department


class CollegeSerializer(serializers.ModelSerializer):
    """学院序列化器"""

    class Meta:
        model = College
        fields = ['id', 'name', 'code', 'sort_order']


class DisciplineSerializer(serializers.ModelSerializer):
    """学科方向序列化器"""

    class Meta:
        model = Discipline
        fields = ['id', 'name', 'category', 'sort_order']


class CategorySerializer(serializers.ModelSerializer):
    """资源类型序列化器（树形，含图标继承）"""
    children = serializers.SerializerMethodField()
    icon = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'parent', 'icon', 'sort_order', 'children']

    def get_icon(self, obj):
        """返回分类图标：一级返回自身图标，二级有则返回否则继承父级图标"""
        if obj.icon:
            return obj.icon
        if obj.parent and obj.parent.icon:
            return obj.parent.icon
        return ''

    def get_children(self, obj):
        children = obj.children.all()
        if children.exists():
            return CategorySerializer(children, many=True).data
        return []


class DepartmentSerializer(serializers.ModelSerializer):
    """部门序列化器（树形结构）"""
    children = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'parent', 'children']

    def get_children(self, obj):
        children = obj.children.all()
        if children.exists():
            return DepartmentSerializer(children, many=True).data
        return []


class FirstLevelCategorySerializer(serializers.ModelSerializer):
    """一级分类序列化器（简化版，供下拉框）"""

    class Meta:
        model = Category
        fields = ['id', 'name', 'icon']


class SecondLevelCategorySerializer(serializers.ModelSerializer):
    """二级分类序列化器（简化版，供联动下拉框）"""

    class Meta:
        model = Category
        fields = ['id', 'name']
