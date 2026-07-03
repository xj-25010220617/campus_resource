from django.conf import settings
from django.db import models


class KnowledgeDoc(models.Model):
    """知识文档——系统核心表，存储知识文档元数据、OSS 信息和索引状态
    
    外键ID对应关系：
    - college_id: 1=计算机与软件学院, 2=智能科学与工程学院, 3=信息与商务管理学院, 4=数字艺术与设计学院, 
                  5=外国语学院, 6=健康医疗科技学院, 7=马克思主义学院, 8=通识教育学院, 9=创新创业学院,
                  10=继续教育学院, 11=国际教育学院, 12=体育教学部, 13=图书馆
    - department_id: 1=校级领导, 2=党政管理部门, 3=教学管理部门, 4=学生工作部门, 5=科研与服务部门,
                      6=后勤保障部门, 7=继续教育与国际教育（二级部门见下方注释）
    - category_id: 1=教学资源, 2=学术资料, 3=管理文件, 4=校园资讯, 5=校园生活, 6=媒体聚焦（二级见下方注释）
    - discipline_id: 1-31 对应各专业方向（见 Discipline 模型）
    """

    STATUS_CHOICES = [
        ('uploaded', '已上传'),
        ('parsing', '解析中'),
        ('chunking', '分块中'),
        ('embedding', '向量化中'),
        ('indexed', '已索引'),
        ('failed', '失败'),
    ]
    SOURCE_TYPE_CHOICES = [
        ('upload', '内部上传'),
        ('website', '官网爬虫'),
        ('library', '图书馆摘要'),
    ]
    FILE_TYPE_CHOICES = [
        ('pdf', 'PDF'),
        ('doc', 'Word'),
        ('ppt', 'PPT'),
        ('xls', 'Excel'),
        ('image', '图片'),
        ('video', '视频'),
        ('audio', '音频'),
        ('md', 'Markdown'),
        ('txt', '纯文本'),
        ('html', 'HTML'),
        ('other', '其他'),
    ]
    VISIBILITY_CHOICES = [
        ('public', '公开'),
        ('private', '私密'),
    ]
    SCOPE_CHOICES = [
        ('school', '全校'),
        ('college', '学院'),
        ('department', '部门'),
    ]

    title = models.CharField(max_length=512, verbose_name='文档标题')
    description = models.TextField(default='', verbose_name='文档描述')
    source_type = models.CharField(
        max_length=16, choices=SOURCE_TYPE_CHOICES,
        db_index=True, verbose_name='来源类型',
    )
    source_url = models.CharField(max_length=1024, default='', verbose_name='来源URL')
    oss_key = models.CharField(max_length=512, default='', verbose_name='OSS对象Key')
    oss_url = models.CharField(max_length=1024, default='', verbose_name='OSS访问URL')
    file_name = models.CharField(max_length=256, default='', verbose_name='原始文件名')
    file_type = models.CharField(
        max_length=16, choices=FILE_TYPE_CHOICES, default='other',
        verbose_name='文件类型',
    )
    visibility = models.CharField(
        max_length=16, choices=VISIBILITY_CHOICES, default='public',
        db_index=True, verbose_name='可见性',
    )
    scope = models.CharField(
        max_length=16, choices=SCOPE_CHOICES, default='college',
        db_index=True, verbose_name='归属范围',
    )
    file_size = models.BigIntegerField(default=0, verbose_name='文件大小（字节）')
    md5_hash = models.CharField(
        max_length=32, default='', db_index=True, verbose_name='MD5哈希',
    )
    page_count = models.IntegerField(default=0, verbose_name='文档页数')
    discipline = models.ForeignKey(
        'classify.Discipline',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='学科方向（id:1=软件工程,2=计算机科学与技术,3=网络工程...）',
    )
    category = models.ForeignKey(
        'classify.Category',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='资源类型（一级:1=教学资源,2=学术资料,3=管理文件,4=校园资讯,5=校园生活,6=媒体聚焦；二级见种子数据）',
    )
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='所属学院（id:1=计算机与软件学院,2=智能科学与工程学院,3=信息与商务管理学院...）',
    )
    department = models.ForeignKey(
        'classify.Department',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='所属部门',
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='uploaded_docs',
        verbose_name='上传者',
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='uploaded',
        db_index=True, verbose_name='状态',
    )
    chunk_count = models.IntegerField(default=0, verbose_name='分块数量')
    is_sensitive = models.BooleanField(default=False, verbose_name='敏感内容标记')
    sensitive_checked = models.BooleanField(default=False, verbose_name='是否已做敏感检查')
    freshness_date = models.DateField(
        null=True, blank=True, db_index=True, verbose_name='内容截止日期',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    indexed_at = models.DateTimeField(null=True, blank=True, verbose_name='索引完成时间')

    class Meta:
        db_table = 'knowledge_doc'
        verbose_name = '知识文档'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Keyword(models.Model):
    """关键词——上传员为文档添加的关键词，权重越高检索命中越优先"""

    MATCH_TYPE_CHOICES = [
        ('exact', '精确匹配'),
        ('fuzzy', '模糊匹配'),
        ('semantic', '语义关联'),
    ]

    doc = models.ForeignKey(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='keywords',
        verbose_name='所属文档',
    )
    phrase = models.CharField(
        max_length=256, db_index=True, verbose_name='关键词或关键语句',
    )
    match_type = models.CharField(
        max_length=16, choices=MATCH_TYPE_CHOICES, default='fuzzy',
        verbose_name='匹配类型',
    )
    weight = models.IntegerField(default=1, verbose_name='权重(1-10)')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_keywords',
        verbose_name='创建人',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'keyword'
        verbose_name = '关键词'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.phrase}（权重:{self.weight}）'


class DocChunk(models.Model):
    """文档切片——文档解析分块后的文本片段，向量存 Milvus，MySQL 存引用"""

    doc = models.ForeignKey(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='chunks',
        verbose_name='所属文档',
    )
    chunk_index = models.IntegerField(verbose_name='切片序号（从0开始）')
    content = models.TextField(verbose_name='切片文本内容')
    token_count = models.IntegerField(default=0, verbose_name='Token数量')
    vector_id = models.CharField(
        max_length=128, db_index=True, verbose_name='Milvus向量ID',
    )
    page_start = models.IntegerField(null=True, blank=True, verbose_name='起始页码')
    page_end = models.IntegerField(null=True, blank=True, verbose_name='结束页码')
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )

    class Meta:
        db_table = 'doc_chunk'
        verbose_name = '文档切片'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=['doc', 'chunk_index'],
                name='uk_chunk_doc_index',
            ),
        ]

    def __str__(self):
        return f'{self.doc.title} - 切片#{self.chunk_index}'
