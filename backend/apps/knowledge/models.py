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
        max_length=16, choices=SCOPE_CHOICES, default='',
        null=True, blank=True, db_index=True,
        verbose_name='归属范围（已废弃，文档归属通过 college_id/department_id 自动确定）',
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
    index_error = models.TextField(
        blank=True, default='', verbose_name='索引错误信息',
    )
    index_attempts = models.PositiveIntegerField(
        default=0, verbose_name='索引尝试次数',
    )
    index_started_at = models.DateTimeField(
        null=True, blank=True, verbose_name='索引开始时间',
    )
    failed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='最终失败时间',
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
    table_schema = models.JSONField(
        null=True, blank=True, verbose_name='表格结构元数据',
        help_text='仅 xlsx/csv 结构化文档：{"sheets":[{"name","columns","row_count","sample_rows"}]}，供表格问答引擎生成 SQL',
    )

    class Meta:
        db_table = 'knowledge_doc'
        verbose_name = '知识文档'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '知识文档表(外键说明: college_id=学院, department_id=部门, discipline_id=学科方向; category_id/scope 已废弃保留)'

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


class Tag(models.Model):
    """标签——人工为文档打上的分类标记，与关键词（Keyword）概念/操作分离。

    标签不带检索权重、不做同义/语义匹配，仅用于浏览、筛选与聚合；
    全平台共享一套词表（无学院隔离），上传时不存在即自动创建。
    """
    name = models.CharField(max_length=64, unique=True, verbose_name='标签名')
    is_deleted = models.BooleanField(default=False, verbose_name='软删除标记')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='删除时间')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'tag'
        verbose_name = '标签'
        verbose_name_plural = verbose_name
        ordering = ['id']
        db_table_comment = '标签字典表(全平台共享，与keyword分离，仅人工打标/浏览筛选聚合，不带检索权重)'

    def __str__(self):
        return self.name


class DocTag(models.Model):
    """文档-标签关联——记录某文档被打上哪些标签及打标人。"""
    doc = models.ForeignKey(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='doc_tags',
        verbose_name='所属文档',
    )
    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name='doc_tags',
        verbose_name='标签',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_doc_tags',
        verbose_name='打标人',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')

    class Meta:
        db_table = 'doc_tag'
        verbose_name = '文档标签'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['doc', 'tag'], name='uk_doc_tag'),
        ]

    def __str__(self):
        return f'{self.doc_id} - {self.tag_id}'


class DocRelation(models.Model):
    """文档关联——文档间有向边（文件网），源文档指向目标文档并带关联类型。"""
    RELATION_TYPE_CHOICES = [
        ('prerequisite', '前置知识'),
        ('supplement', '补充材料'),
        ('same_course', '同课相关'),
        ('citation', '引用来源'),
        ('replaces', '替代/新版'),
    ]

    source_doc = models.ForeignKey(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='outgoing_relations',
        verbose_name='源文档',
    )
    target_doc = models.ForeignKey(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='incoming_relations',
        verbose_name='目标文档',
    )
    relation_type = models.CharField(
        max_length=16, choices=RELATION_TYPE_CHOICES, default='supplement',
        verbose_name='关联类型',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_doc_relations',
        verbose_name='建边人',
    )
    is_deleted = models.BooleanField(default=False, verbose_name='软删除标记')
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='删除时间')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'doc_relation'
        verbose_name = '文档关联'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '文档关联表(文件网有向边，is_deleted=1表示撤边；关联目标需在用户可浏览范围内)'
        constraints = [
            models.UniqueConstraint(
                fields=['source_doc', 'target_doc', 'relation_type'],
                name='uk_doc_relation',
            ),
        ]
        indexes = [
            models.Index(fields=['source_doc'], name='idx_rel_source'),
            models.Index(fields=['target_doc'], name='idx_rel_target'),
        ]

    def __str__(self):
        return f'{self.source_doc_id} -{self.relation_type}-> {self.target_doc_id}'


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


class ChunkedUploadSession(models.Model):
    """分片上传会话——大文件分片上传的状态记录，分片数据存于 OSS Multipart Upload"""
    STATUS_CHOICES = [
        ('uploading', '上传中'),
        ('completed', '已完成'),
        ('aborted', '已取消'),
    ]

    upload_id = models.CharField(
        max_length=128, primary_key=True, verbose_name='OSS分片上传ID',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='chunked_uploads', verbose_name='上传用户',
    )
    file_name = models.CharField(max_length=256, verbose_name='原始文件名')
    file_size = models.BigIntegerField(default=0, verbose_name='文件大小（字节）')
    file_md5 = models.CharField(
        max_length=32, db_index=True, verbose_name='文件MD5哈希',
    )
    oss_key = models.CharField(max_length=512, verbose_name='OSS对象Key')
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='uploading',
        db_index=True, verbose_name='状态',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'chunked_upload_session'
        verbose_name = '分片上传会话'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '大文件分片上传会话表(分片数据存OSS，本表只存状态；status=uploading且超时的会话由定时任务清理)'
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_cus_user_status'),
            models.Index(fields=['file_md5', 'status'], name='idx_cus_md5_status'),
        ]

    def __str__(self):
        return f'{self.user.username} 分片上传 {self.file_name} [{self.status}]'
