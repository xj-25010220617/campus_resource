"""Resources - 资源序列化器"""
from rest_framework import serializers

from .models import Resource, UploadLog


class ResourceListSerializer(serializers.ModelSerializer):
    """资源列表序列化器（含分类完整路径、图标、关键词）"""
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)
    college_name = serializers.CharField(source='college.name', read_only=True)
    discipline_name = serializers.CharField(source='discipline.name', read_only=True)
    category_name = serializers.SerializerMethodField()
    category_icon = serializers.SerializerMethodField()

    class Meta:
        model = Resource
        fields = [
            'id', 'title', 'description', 'file_type', 'file_size',
            'uploader', 'uploader_name', 'college', 'college_name',
            'discipline', 'discipline_name', 'category', 'category_name',
            'category_icon', 'keywords',
            'status', 'visibility', 'view_count', 'download_count',
            'created_at', 'published_at',
        ]
        read_only_fields = fields

    def get_category_name(self, obj):
        """返回分类完整路径，如 "教学资源 > 教学课件" """
        if obj.category.parent:
            return f'{obj.category.parent.name} > {obj.category.name}'
        return obj.category.name

    def get_category_icon(self, obj):
        """返回分类图标：二级有则返回，否则继承父级图标"""
        if obj.category.icon:
            return obj.category.icon
        if obj.category.parent and obj.category.parent.icon:
            return obj.category.parent.icon
        return ''


class ResourceDetailSerializer(ResourceListSerializer):
    """资源详情序列化器"""

    class Meta(ResourceListSerializer.Meta):
        fields = ResourceListSerializer.Meta.fields + [
            'file_name', 'oss_url', 'keywords',
            'target_audience', 'course_term',
            'audit_comment', 'auditor',
        ]


class UploadLogSerializer(serializers.ModelSerializer):
    """上传日志序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    college_name = serializers.CharField(source='college.name', read_only=True)
    resource_title = serializers.CharField(source='resource.title', read_only=True, allow_null=True)

    class Meta:
        model = UploadLog
        fields = [
            'id', 'user', 'user_name', 'resource', 'resource_title',
            'file_name', 'file_size', 'file_type', 'college', 'college_name',
            'status', 'error_message', 'ip_address', 'user_agent',
            'created_at', 'completed_at',
        ]
        read_only_fields = fields


class UploadLogStatsSerializer(serializers.Serializer):
    """上传统计序列化器"""
    total_count = serializers.IntegerField()
    success_count = serializers.IntegerField()
    failed_count = serializers.IntegerField()
    total_size = serializers.IntegerField()
    by_status = serializers.DictField()
    by_file_type = serializers.DictField()
    by_college = serializers.ListField()
    by_user = serializers.ListField()


class UploadLogCompleteSerializer(serializers.Serializer):
    """更新上传日志状态序列化器"""
    status = serializers.ChoiceField(
        choices=['success', 'failed'],
        help_text='状态：success=成功，failed=失败'
    )
    resource_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text='成功时关联的资源ID'
    )
    error_message = serializers.CharField(
        required=False, allow_blank=True, max_length=1000,
        help_text='失败时的错误信息'
    )

    def validate(self, attrs):
        status_val = attrs.get('status')
        if status_val == 'success':
            if 'resource_id' not in attrs or attrs.get('resource_id') is None:
                raise serializers.ValidationError({'resource_id': '成功状态必须提供 resource_id'})
        if status_val == 'failed':
            if not attrs.get('error_message'):
                raise serializers.ValidationError({'error_message': '失败状态必须提供 error_message'})
        return attrs
