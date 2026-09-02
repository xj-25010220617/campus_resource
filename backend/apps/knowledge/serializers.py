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
    uploader_college_name = serializers.CharField(
        source='uploader.college.name', read_only=True, default='',
    )
    uploader_department_name = serializers.CharField(
        source='uploader.department.name', read_only=True, default='',
    )

    class Meta:
        model = KnowledgeDoc
        fields = [
            'id', 'title', 'file_type', 'file_size', 'status',
            'college_name', 'discipline_name', 'uploader_name',
            'uploader_college_name', 'uploader_department_name',
            'chunk_count', 'created_at',
        ]


# 文件大小限制常量（字节）
FILE_SIZE_LIMITS = {
    # 文档类
    'pdf': 50 * 1024 * 1024,                          # PDF — 50MB（多页扫描件/图纸）
    'doc': 30 * 1024 * 1024, 'docx': 30 * 1024 * 1024,  # Word — 30MB
    'xls': 30 * 1024 * 1024, 'xlsx': 30 * 1024 * 1024,  # Excel — 30MB
    'ppt': 50 * 1024 * 1024, 'pptx': 50 * 1024 * 1024,  # PPT — 50MB（含高清图汇报）
    'txt': 50 * 1024 * 1024, 'md': 50 * 1024 * 1024, 'html': 50 * 1024 * 1024,
    # 图片类 — 30MB（相机原图可放宽至此档）
    'png': 30 * 1024 * 1024, 'jpg': 30 * 1024 * 1024, 'jpeg': 30 * 1024 * 1024,
    'gif': 30 * 1024 * 1024, 'webp': 30 * 1024 * 1024,
    # 压缩包 — 80MB（多文件打包场景）
    'zip': 80 * 1024 * 1024, 'rar': 80 * 1024 * 1024,
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


# 扩展名 → 文件类型类别映射（与 KnowledgeDoc.FILE_TYPE_CHOICES 对齐）
# KnowledgeDoc.file_type 实际存储「类别」（video/image/audio/pdf/doc/…），
# 而上传时前端/文件名拿到的是「扩展名」（mp4/jpg/docx/…），此处统一归一化。
EXT_TO_FILE_TYPE = {
    # 文档类
    'doc': 'doc', 'docx': 'doc',
    'ppt': 'ppt', 'pptx': 'ppt',
    'xls': 'xls', 'xlsx': 'xls',
    'pdf': 'pdf',
    'md': 'md', 'txt': 'txt',
    'html': 'html', 'htm': 'html',
    # 图片类
    'png': 'image', 'jpg': 'image', 'jpeg': 'image',
    'gif': 'image', 'webp': 'image',
    # 音频类
    'mp3': 'audio', 'wav': 'audio', 'ogg': 'audio',
    'flac': 'audio', 'aac': 'audio', 'm4a': 'audio',
    # 视频类
    'mp4': 'video', 'webm': 'video', 'mov': 'video',
    'avi': 'video', 'mkv': 'video', 'flv': 'video',
    # 压缩包（归档类归入 other）
    'zip': 'other', 'rar': 'other',
}


def normalize_file_type(file_type: str) -> str:
    """将文件类型归一化为 KnowledgeDoc.FILE_TYPE_CHOICES 中的类别。

    兼容两种入参（幂等）：
    - 已是类别（video/image/audio/pdf/doc/…）→ 原样返回
    - 扩展名（mp4/avi/mkv/docx/jpg/…）→ 映射到对应类别
    未知扩展名兜底为 'other'。
    """
    ft = (file_type or 'other').strip().lower()
    if ft in dict(KnowledgeDoc.FILE_TYPE_CHOICES):
        return ft
    return EXT_TO_FILE_TYPE.get(ft, 'other')


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
    title = serializers.CharField(
        max_length=512, required=False, default='',
        help_text='文档标题（可选，未提供时由 AI 生成）',
    )
    description = serializers.CharField(
        default='', allow_blank=True, help_text='文档描述（可选，未提供时 AI 生成）',
    )
    file_name = serializers.CharField(max_length=256, help_text='原始文件名')
    file_size = serializers.IntegerField(min_value=0, help_text='文件大小（字节）')
    file_type = serializers.CharField(max_length=16, help_text='文件类型（pdf/doc/ppt/xls/image/video/audio/md/txt/html/other）')
    md5 = serializers.CharField(max_length=32, help_text='文件MD5哈希值')
    college_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属学院ID（默认取上传者学院）')
    department_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属部门ID（默认取上传者部门）')
    discipline_id = serializers.IntegerField(required=False, allow_null=True, help_text='学科方向ID（可选）')
    keywords = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text='关键词列表（可选，未提供时 AI 提取）',
    )
    visibility = serializers.ChoiceField(
        choices=['public', 'private'],
        default='public',
        help_text='可见性（public:公开 / private:私密），默认 public',
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


def _sanitize_content(value: str) -> str:
    """清理文本内容中的控制字符，防止 JSON 解析错误"""
    # 移除不可见的控制字符（保留换行符和制表符）
    import re
    # 移除 ASCII 控制字符（0x00-0x1F），但保留换行符(0x0A)、回车符(0x0D)、制表符(0x09)
    cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', value)
    return cleaned


class TextUploadSerializer(serializers.Serializer):
    """管理员手动录入文本请求参数"""

    title = serializers.CharField(
        max_length=512, required=False, default='',
        help_text='文档标题（可选，未提供时由 AI 自动生成）',
    )
    content = serializers.CharField(help_text='文本内容', validators=[_sanitize_content])
    description = serializers.CharField(
        default='', allow_blank=True, help_text='文档描述（可选，未提供时 AI 生成）',
    )
    college_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属学院ID（默认取上传者学院）')
    department_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属部门ID（默认取上传者部门）')
    discipline_id = serializers.IntegerField(required=False, allow_null=True, help_text='学科方向ID（可选）')
    keywords = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text='关键词列表（可选，未提供时 AI 提取）',
    )
    visibility = serializers.ChoiceField(
        choices=['public', 'private'],
        default='public',
        help_text='可见性（public:公开 / private:私密），默认 public',
    )


class FileUploadSerializer(serializers.Serializer):
    """文件上传请求参数（multipart/form-data）"""

    file = serializers.FileField(help_text='上传的文件')
    title = serializers.CharField(
        max_length=512, required=False, default='',
        help_text='文档标题（可选，默认使用文件名，或由 AI 自动生成）',
    )
    description = serializers.CharField(
        default='', allow_blank=True, help_text='文档描述（可选，未提供时 AI 生成）',
    )
    college_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属学院ID（默认取上传者学院）')
    department_id = serializers.IntegerField(required=False, allow_null=True, help_text='所属部门ID（默认取上传者部门）')
    discipline_id = serializers.IntegerField(required=False, allow_null=True, help_text='学科方向ID（可选）')
    keywords = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text='关键词列表（可选，未提供时 AI 提取）',
    )
    visibility = serializers.ChoiceField(
        choices=['public', 'private'],
        default='public',
        help_text='可见性（public:公开 / private:私密），默认 public',
    )


class AIClassifySerializer(serializers.Serializer):
    """AI 智能分类推荐请求参数"""

    title = serializers.CharField(
        max_length=512, required=False, default='',
        help_text='文件标题（可选，为空时 AI 根据内容生成）',
    )
    content = serializers.CharField(
        default='', allow_blank=True, help_text='文本内容片段（可选，建议前 2000 字）',
    )


class BatchDeleteSerializer(serializers.Serializer):
    """批量删除请求参数"""
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), min_length=1, max_length=100,
        help_text='要删除的文档 ID 列表，最多 100 条',
    )
