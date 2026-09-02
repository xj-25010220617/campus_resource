"""Resources - 上传日志序列化器"""
from rest_framework import serializers

from .models import UploadLog


class UploadLogSerializer(serializers.ModelSerializer):
    """上传日志序列化器"""
    user_name = serializers.CharField(source='user.username', read_only=True)
    college_name = serializers.CharField(source='college.name', read_only=True)
    knowledge_doc_title = serializers.CharField(source='knowledge_doc.title', read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(format='%Y/%m/%d %H:%M', read_only=True)
    completed_at = serializers.DateTimeField(format='%Y/%m/%d %H:%M', read_only=True)

    class Meta:
        model = UploadLog
        fields = [
            'id', 'user', 'user_name', 'knowledge_doc', 'knowledge_doc_title',
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
    status = serializers.ChoiceField(choices=['success', 'failed'])
    knowledge_doc_id = serializers.IntegerField(required=False, allow_null=True)
    error_message = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate(self, attrs):
        if attrs.get('status') == 'success' and attrs.get('knowledge_doc_id') is None:
            raise serializers.ValidationError({'knowledge_doc_id': '成功状态必须提供 knowledge_doc_id'})
        if attrs.get('status') == 'failed' and not attrs.get('error_message'):
            raise serializers.ValidationError({'error_message': '失败时必须提供 error_message'})
        return attrs
