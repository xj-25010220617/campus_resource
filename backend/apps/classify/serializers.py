from rest_framework import serializers

from apps.accounts.models import College
from .models import Discipline, Category


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
    """资源类型序列化器（树形）"""
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'parent', 'sort_order', 'children']

    def get_children(self, obj):
        children = obj.children.all()
        if children.exists():
            return CategorySerializer(children, many=True).data
        return []
