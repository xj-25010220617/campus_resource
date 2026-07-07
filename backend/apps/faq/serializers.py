from rest_framework import serializers

from .models import FAQCategory, FAQGenerationLog, FAQItem


class FAQCategorySerializer(serializers.ModelSerializer):
    """FAQ 分类序列化器（公共）"""

    class Meta:
        model = FAQCategory
        fields = ['id', 'name', 'icon', 'sort_order']


class FAQItemListSerializer(serializers.ModelSerializer):
    """FAQ 列表序列化器（公共 — 只有已发布的）"""
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    college_name = serializers.CharField(source='college.name', read_only=True, allow_null=True)

    class Meta:
        model = FAQItem
        fields = [
            'id', 'question', 'answer', 'category', 'category_name',
            'college', 'college_name', 'tags', 'frequency',
            'status', 'created_at',
        ]


class FAQItemDetailSerializer(serializers.ModelSerializer):
    """FAQ 详情序列化器（公共）"""
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    college_name = serializers.CharField(source='college.name', read_only=True, allow_null=True)

    class Meta:
        model = FAQItem
        fields = [
            'id', 'question', 'answer', 'category', 'category_name',
            'college', 'college_name', 'tags', 'frequency', 'variant_count',
            'source_period_start', 'source_period_end', 'created_at', 'updated_at',
        ]


class FAQItemDraftSerializer(serializers.ModelSerializer):
    """草稿详情序列化器（管理端）"""

    class Meta:
        model = FAQItem
        fields = [
            'id', 'question', 'answer', 'category', 'college',
            'tags', 'status', 'frequency', 'variant_count',
            'source_period_start', 'source_period_end',
            'related_doc_ids', 'reviewed_by', 'reviewed_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'status', 'frequency', 'variant_count',
            'source_period_start', 'source_period_end',
            'related_doc_ids', 'reviewed_by', 'reviewed_at',
            'created_at', 'updated_at',
        ]


class FAQItemPublishSerializer(serializers.Serializer):
    """发布/驳回操作序列化器（管理端）"""
    action = serializers.ChoiceField(choices=['publish', 'reject'])


class FAQGenerationLogSerializer(serializers.ModelSerializer):
    """FAQ 生成日志序列化器（管理端）"""
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = FAQGenerationLog
        fields = [
            'id', 'status',
            'total_questions', 'clusters_found', 'drafts_generated',
            'error_message',
            'started_at', 'completed_at', 'duration_seconds',
        ]

    def get_duration_seconds(self, obj):
        if obj.completed_at and obj.started_at:
            return int((obj.completed_at - obj.started_at).total_seconds())
        return None
