from rest_framework import serializers

from .models import KnowledgeDoc, Keyword


class KnowledgeDocSerializer(serializers.ModelSerializer):
    """知识文档详情序列化器"""

    # 关联字段展平（只读）
    discipline_name = serializers.CharField(source='discipline.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    college_name = serializers.CharField(source='college.name', read_only=True)
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)

    class Meta:
        model = KnowledgeDoc
        fields = [
            'id', 'title', 'description', 'source_type', 'source_url',
            'oss_key', 'oss_url', 'file_name', 'file_type', 'file_size',
            'md5_hash', 'page_count',
            'discipline', 'discipline_name',
            'category', 'category_name',
            'college', 'college_name',
            'uploader', 'uploader_name',
            'status', 'chunk_count', 'is_sensitive', 'sensitive_checked',
            'freshness_date',
            'created_at', 'updated_at', 'indexed_at',
        ]
        read_only_fields = ['id', 'chunk_count', 'created_at', 'updated_at', 'indexed_at']


class KnowledgeDocListSerializer(serializers.ModelSerializer):
    """知识文档列表序列化器（简化版，用于列表页展示）"""

    college_name = serializers.CharField(source='college.name', read_only=True)
    discipline_name = serializers.CharField(source='discipline.name', read_only=True)
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)

    class Meta:
        model = KnowledgeDoc
        fields = [
            'id', 'title', 'file_type', 'file_size', 'status',
            'college_name', 'discipline_name', 'uploader_name',
            'chunk_count', 'created_at',
        ]


class UploadSTSRequestSerializer(serializers.Serializer):
    """获取OSS上传凭证请求参数"""

    file_name = serializers.CharField(max_length=256, help_text='原始文件名')
    file_size = serializers.IntegerField(min_value=0, help_text='文件大小（字节）')
    md5 = serializers.CharField(max_length=32, help_text='文件MD5哈希值')


class UploadCallbackSerializer(serializers.Serializer):
    """OSS上传完成回调请求参数"""

    oss_key = serializers.CharField(max_length=512, help_text='OSS对象Key')
    title = serializers.CharField(max_length=512, help_text='文档标题')
    description = serializers.CharField(default='', allow_blank=True, help_text='文档描述')
    file_name = serializers.CharField(max_length=256, help_text='原始文件名')
    file_size = serializers.IntegerField(min_value=0, help_text='文件大小（字节）')
    file_type = serializers.CharField(max_length=16, help_text='文件类型（pdf/doc/ppt/xls/image/video/audio/md/txt/html/other）')
    md5 = serializers.CharField(max_length=32, help_text='文件MD5哈希值')
    discipline_id = serializers.IntegerField(required=False, allow_null=True, help_text='学科方向ID（选填）')
    category_id = serializers.IntegerField(required=False, allow_null=True, help_text='资源类型ID（选填）')
    college_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属学院ID（选填）')


class KeywordSerializer(serializers.ModelSerializer):
    """关键词序列化器"""

    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = Keyword
        fields = [
            'id', 'phrase', 'match_type', 'weight',
            'created_by_name', 'created_at',
        ]
        read_only_fields = ['id', 'created_by_name', 'created_at']


class KeywordCreateSerializer(serializers.Serializer):
    """创建关键词请求参数"""

    phrase = serializers.CharField(max_length=256, help_text='关键词或关键语句')
    match_type = serializers.CharField(
        max_length=16, default='fuzzy',
        help_text='匹配类型（exact:精确匹配 / fuzzy:模糊匹配 / semantic:语义关联）',
    )
    weight = serializers.IntegerField(
        default=1, min_value=1, max_value=10,
        help_text='权重(1-10)，越高检索时越优先',
    )
