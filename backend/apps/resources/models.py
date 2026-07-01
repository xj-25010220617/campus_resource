from django.db import models
from django.conf import settings


class Resource(models.Model):
    """资源（核心表）"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('pending', '待审核'),
        ('published', '已发布'),
        ('rejected', '已退回'),
        ('archived', '已归档'),
    ]
    FILE_TYPE_CHOICES = [
        ('pdf', 'PDF'),
        ('doc', 'Word'),
        ('ppt', 'PowerPoint'),
        ('xls', 'Excel'),
        ('image', '图片'),
        ('video', '视频'),
        ('audio', '音频'),
        ('other', '其他'),
    ]

    title = models.CharField(max_length=256, verbose_name='资源标题')
    description = models.TextField(blank=True, default='', verbose_name='资源描述')

    # OSS 存储字段
    oss_key = models.CharField(max_length=512, verbose_name='OSS对象Key')
    oss_url = models.URLField(max_length=1024, verbose_name='OSS访问URL')
    file_name = models.CharField(max_length=256, verbose_name='原始文件名')
    file_size = models.BigIntegerField(verbose_name='文件大小（字节）')
    file_type = models.CharField(
        max_length=16, choices=FILE_TYPE_CHOICES, verbose_name='文件类型'
    )
    md5_hash = models.CharField(
        max_length=32, db_index=True, verbose_name='MD5哈希（去重）'
    )

    # 分类关联
    discipline = models.ForeignKey(
        'classify.Discipline', on_delete=models.PROTECT, verbose_name='学科方向'
    )
    category = models.ForeignKey(
        'classify.Category', on_delete=models.PROTECT, verbose_name='资源类型'
    )

    # 状态与归属
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='draft',
        db_index=True, verbose_name='状态'
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='resources', verbose_name='上传者'
    )
    college = models.ForeignKey(
        'accounts.College', on_delete=models.PROTECT, verbose_name='所属学院'
    )

    # 审核信息
    audit_comment = models.TextField(blank=True, default='', verbose_name='审核意见')
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='audited_resources',
        verbose_name='审核人'
    )

    # 统计
    view_count = models.IntegerField(default=0, verbose_name='浏览量')
    download_count = models.IntegerField(default=0, verbose_name='下载量')

    # RAG 检索增强字段
    visibility = models.CharField(
        max_length=16,
        choices=[
            ('public', '公开'),
            ('internal', '校内'),
            ('restricted', '授权'),
        ],
        default='public',
        db_index=True,
        verbose_name='可见层级',
    )
    target_audience = models.CharField(
        max_length=16,
        choices=[
            ('all', '全部'),
            ('undergrad', '本科生'),
            ('postgrad', '研究生'),
            ('teacher', '教师'),
            ('staff', '职工'),
        ],
        default='all',
        verbose_name='适用对象',
    )
    course_term = models.CharField(
        max_length=128, blank=True, default='',
        db_index=True, verbose_name='课程学期',
    )
    chunk_count = models.IntegerField(
        default=0, verbose_name='分块数量（RAG）',
    )
    keywords = models.JSONField(
        default=list, blank=True, verbose_name='关键词列表',
    )

    # 时间
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    published_at = models.DateTimeField(null=True, blank=True, verbose_name='发布时间')

    class Meta:
        db_table = 'resource'
        verbose_name = '资源'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['discipline', 'college'], name='idx_res_disc_college'),
            models.Index(fields=['status', 'created_at'], name='idx_res_status_created'),
            models.Index(fields=['uploader', 'created_at'], name='idx_res_uploader_created'),
            models.Index(fields=['visibility'], name='idx_res_visibility'),
            models.Index(fields=['course_term'], name='idx_res_course_term'),
        ]

    def __str__(self):
        return self.title


class Chunk(models.Model):
    """文档分块表（RAG向量化用的chunk，Milvus同步）"""
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('embedding', '向量化中'),
        ('indexed', '已入库'),
        ('failed', '失败'),
    ]

    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE,
        related_name='chunks', verbose_name='所属资源'
    )
    chunk_index = models.IntegerField(verbose_name='分块序号（从0开始）')
    chunk_text = models.TextField(verbose_name='分块文本内容')
    page_number = models.IntegerField(default=0, verbose_name='页码')
    seq_order = models.IntegerField(default=0, verbose_name='顺序号')
    vector_id = models.CharField(
        max_length=128, blank=True, default='',
        db_index=True, verbose_name='Milvus向量ID'
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='pending',
        db_index=True, verbose_name='同步状态'
    )
    char_count = models.IntegerField(default=0, verbose_name='字符数')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    indexed_at = models.DateTimeField(null=True, blank=True, verbose_name='入库时间')

    class Meta:
        db_table = 'chunk'
        verbose_name = '文档分块'
        verbose_name_plural = verbose_name
        ordering = ['resource_id', 'chunk_index']
        unique_together = ['resource', 'chunk_index']
        indexes = [
            models.Index(fields=['resource', 'chunk_index'], name='idx_chunk_res_index'),
            models.Index(fields=['vector_id'], name='idx_chunk_vector'),
            models.Index(fields=['status'], name='idx_chunk_status'),
        ]

    def __str__(self):
        return f'Chunk #{self.chunk_index} of {self.resource_id}'


class Favorite(models.Model):
    """资源收藏"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='favorites', verbose_name='用户'
    )
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE,
        related_name='favorites', verbose_name='资源'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='收藏时间')

    class Meta:
        db_table = 'favorite'
        verbose_name = '收藏'
        verbose_name_plural = verbose_name
        unique_together = ['user', 'resource']
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} 收藏了 {self.resource.title}'
