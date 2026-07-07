from django.conf import settings
from django.db import models


class KnowledgeDoc(models.Model):
    """知识文档——系统核心表，存储知识文档元数据、OSS 信息和索引状态"""
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
        verbose_name='学科方向',
        db_comment='学科方向(1=软件工程,2=计算机,3=网络,4=物联网,5=网安,6=集成电路,7=大数据中外,8=大数据管理,9=机器人,10=信息工程,11=智能科学,12=数据科学,13=AI,14=信管,15=供应链,16=人力,17=电商,18=财务,19=数媒技术,20=数媒艺术,21=艺术与科技,22=VR,23=工业设计,24=视觉传达,25=动画,26=英语,27=商务英语,28=日语,29=公共外语,30=留学生,31=智能医学,32=医学信息,33=医学影像,34=健康服务,35=数字动画,36=软件技术,37=AI技术,38=医养照护)',
    )
    category = models.ForeignKey(
        'classify.Category',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='资源类型',
        db_comment='资源类型(1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)',
    )
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='所属学院',
        db_comment='所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)',
    )
    department = models.ForeignKey(
        'classify.Department',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knowledge_docs',
        verbose_name='所属部门',
        db_comment='所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)',
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
        db_table_comment = '知识文档表(外键说明: college_id=学院, department_id=部门, discipline_id=学科方向; category_id 已废弃保留)'

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
