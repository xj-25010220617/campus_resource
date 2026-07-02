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
        read_only_fields = [
            'id', 'source_type', 'oss_key', 'oss_url', 'file_name', 'file_type',
            'file_size', 'md5_hash', 'page_count', 'uploader',
            'chunk_count', 'created_at', 'updated_at', 'indexed_at',
        ]


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


# 文件大小限制常量（字节）
FILE_SIZE_LIMITS = {
    # 文档类 — 50MB
    'pdf': 50 * 1024 * 1024, 'doc': 50 * 1024 * 1024, 'docx': 50 * 1024 * 1024,
    'ppt': 50 * 1024 * 1024, 'pptx': 50 * 1024 * 1024,
    'xls': 50 * 1024 * 1024, 'xlsx': 50 * 1024 * 1024,
    'txt': 50 * 1024 * 1024, 'md': 50 * 1024 * 1024, 'html': 50 * 1024 * 1024,
    # 图片类 — 10MB
    'png': 10 * 1024 * 1024, 'jpg': 10 * 1024 * 1024, 'jpeg': 10 * 1024 * 1024,
    'gif': 10 * 1024 * 1024, 'webp': 10 * 1024 * 1024,
    # 音频类 — 50MB
    'mp3': 50 * 1024 * 1024, 'wav': 50 * 1024 * 1024, 'ogg': 50 * 1024 * 1024,
    'flac': 50 * 1024 * 1024, 'aac': 50 * 1024 * 1024, 'm4a': 50 * 1024 * 1024,
    # 视频类 — 500MB
    'mp4': 500 * 1024 * 1024, 'webm': 500 * 1024 * 1024, 'mov': 500 * 1024 * 1024,
    'avi': 500 * 1024 * 1024, 'mkv': 500 * 1024 * 1024, 'flv': 500 * 1024 * 1024,
    # 其他 — 50MB
    'other': 50 * 1024 * 1024,
}

# 允许上传的文件类型白名单
ALLOWED_FILE_TYPES = {
    # 文档类
    'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx',
    'txt', 'md', 'html',
    # 图片类
    'png', 'jpg', 'jpeg', 'gif', 'webp',
    # 音频类
    'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a',
    # 视频类
    'mp4', 'webm', 'mov', 'avi', 'mkv', 'flv',
    # 压缩包类
    'zip', 'rar',
    # 其他（兜底）
    'other',
}


def _validate_file_size(file_size: int, file_type: str) -> None:
    """校验文件大小是否在允许范围内，超限抛出 ValidationError"""
    ft = (file_type or 'other').lower()
    max_size = FILE_SIZE_LIMITS.get(ft, FILE_SIZE_LIMITS['other'])
    if file_size > max_size:
        max_mb = max_size // (1024 * 1024)
        cur_mb = file_size // (1024 * 1024)
        raise serializers.ValidationError(
            f'{ft} 文件大小不能超过 {max_mb}MB，当前大小 {cur_mb}MB'
        )


def _validate_file_type(file_type: str) -> None:
    """校验文件类型是否在允许范围内，不支持则抛出 ValidationError"""
    ft = (file_type or 'other').lower()
    if ft not in ALLOWED_FILE_TYPES:
        raise serializers.ValidationError(
            f'不支持的文件类型 "{ft}"，支持的类型：{", ".join(sorted(ALLOWED_FILE_TYPES))}'
        )


class UploadSTSRequestSerializer(serializers.Serializer):
    """获取OSS上传凭证请求参数"""

    file_name = serializers.CharField(max_length=256, help_text='原始文件名')
    file_size = serializers.IntegerField(min_value=0, help_text='文件大小（字节）')
    file_type = serializers.CharField(
        max_length=16, default='other', help_text='文件类型（用于大小限制校验）',
    )
    md5 = serializers.CharField(max_length=32, help_text='文件MD5哈希值')

    def validate_file_size(self, value):
        _validate_file_size(value, self.initial_data.get('file_type', 'other'))
        return value

    def validate_file_type(self, value):
        _validate_file_type(value)
        return value


class UploadCallbackSerializer(serializers.Serializer):
    """OSS上传完成回调请求参数"""

    oss_key = serializers.CharField(max_length=512, help_text='OSS对象Key')
    title = serializers.CharField(max_length=512, help_text='文档标题')
    description = serializers.CharField(default='', allow_blank=True, help_text='文档描述')
    file_name = serializers.CharField(max_length=256, help_text='原始文件名')
    file_size = serializers.IntegerField(min_value=0, help_text='文件大小（字节）')
    file_type = serializers.CharField(max_length=16, help_text='文件类型（pdf/doc/ppt/xls/image/video/audio/md/txt/html/other）')
    md5 = serializers.CharField(max_length=32, help_text='文件MD5哈希值')
    discipline_id = serializers.IntegerField(help_text='学科方向ID')
    category_id = serializers.IntegerField(help_text='资源类型ID（必须是二级分类）')
    college_id = serializers.IntegerField(help_text='所属学院ID')
    keywords = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text='关键词列表（可选）',
    )
    visibility = serializers.CharField(
        max_length=16, default='public',
        help_text='可见性（public:公开 / private:私密）',
    )

    def validate_file_size(self, value):
        _validate_file_size(value, self.initial_data.get('file_type', 'other'))
        return value

    def validate_file_type(self, value):
        _validate_file_type(value)
        return value


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


class TextUploadSerializer(serializers.Serializer):
    """管理员手动录入文本请求参数"""

    title = serializers.CharField(max_length=512, help_text='文档标题')
    content = serializers.CharField(help_text='文本内容')
    description = serializers.CharField(
        default='', allow_blank=True, help_text='文档描述（可选）',
    )
    college_id = serializers.IntegerField(help_text='学院ID')
    discipline_id = serializers.IntegerField(help_text='学科方向ID')
    category_id = serializers.IntegerField(help_text='资源类型ID（必须是二级分类）')
    keywords = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text='关键词列表（可选）',
    )
    visibility = serializers.CharField(
        max_length=16, default='public',
        help_text='可见性（public:公开 / private:私密）',
    )


class FileUploadSerializer(serializers.Serializer):
    """文件上传请求参数（multipart/form-data）"""

    file = serializers.FileField(help_text='上传的文件')
    title = serializers.CharField(max_length=512, help_text='文档标题')
    description = serializers.CharField(
        default='', allow_blank=True, help_text='文档描述（可选）',
    )
    college_id = serializers.IntegerField(help_text='学院ID')
    discipline_id = serializers.IntegerField(help_text='学科方向ID')
    category_id = serializers.IntegerField(help_text='资源类型ID（必须是二级分类）')
    keywords = serializers.CharField(
        default='', allow_blank=True,
        help_text='关键词 JSON 数组，如 ["关键词1","关键词2"]（可选）',
    )
    visibility = serializers.CharField(
        max_length=16, default='public',
        help_text='可见性（public:公开 / private:私密）',
    )
