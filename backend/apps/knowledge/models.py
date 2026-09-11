from django.conf import settings
from django.db import models

from .moderation import (
    MODERATION_STATE_CHOICES,
    MODERATION_STATE_PENDING,
    STORAGE_STATE_CHOICES,
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_QUARANTINE,
    VERDICT_CHOICES,
    is_indexable_moderation_state,
)
from .upload_intent import (
    UPLOAD_INTENT_ISSUED,
    UPLOAD_INTENT_STATUS_CHOICES,
)


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


class UploadIntent(models.Model):
    """上传意图——服务端签发的**上传凭证**，把"谁在传 / 传到哪个隔离区对象"钉死。

    存在意义（对应 P1-2D 的安全边界）
    ---------------------------------
    没有这张表时，STS 直传回调只能校验客户端回传 oss_key 的**格式与 scope 前缀**，
    无法证明该对象由本次请求的发起者上传——同一 scope 目录下可以直接挂载他人的对象。
    UploadIntent 让服务端在签发阶段就写下 `(user, quarantine_key)` 的绑定关系，
    回调阶段只认 intent id，key 与归属都以库里的记录为准。

    生命周期
    --------
        issued ──complete──> completed ──consume──> consumed
          │                      │
          └────────expire────────┴──> expired
          └────────cancel────────────> cancelled

    - `completed_at` 与 `consumed_at` 语义不重叠，故两个字段都保留：
      `completed_at` = 对象已确认存在于隔离区（尚未建任何业务记录）；
      `consumed_at`  = 业务已基于该对象建过记录，此 intent 不能再次消费。
    - 状态**只能由服务端**在 `apps.knowledge.upload_intent` 内变更；
      客户端提交的任何状态字段都必须被忽略。

    Key 约定
    --------
    `quarantine_key` 由 `utils.oss.build_quarantine_key()` 生成，**必须**是
    `quarantine/` 命名空间下的对象，**不得**是 `knowledge/`。
    设计上不提供任何"客户端指定 key"的入口，且该字段在 service 层无更新路径。
    """

    scope = models.CharField(
        max_length=16, choices=KnowledgeDoc.SCOPE_CHOICES,
        verbose_name='归属范围',
    )
    owner_id = models.PositiveIntegerField(
        default=0, verbose_name='归属对象ID（与 scope 配合，school 为 0）',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='upload_intents', verbose_name='上传用户',
    )
    quarantine_key = models.CharField(
        max_length=512, unique=True, verbose_name='隔离区对象 Key（服务端生成）',
    )
    original_filename = models.CharField(max_length=256, verbose_name='原始文件名')
    content_type = models.CharField(
        max_length=128, blank=True, default='', verbose_name='声明的 MIME 类型',
    )
    expected_size = models.BigIntegerField(
        null=True, blank=True, verbose_name='声明的大小（字节）',
    )
    actual_size = models.BigIntegerField(
        null=True, blank=True, verbose_name='服务端核实到的大小（字节）',
    )
    status = models.CharField(
        max_length=16, choices=UPLOAD_INTENT_STATUS_CHOICES,
        default=UPLOAD_INTENT_ISSUED, db_index=True, verbose_name='状态',
    )
    expires_at = models.DateTimeField(db_index=True, verbose_name='过期时间')
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='上传完成时间',
    )
    consumed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='被业务消费时间',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'upload_intent'
        verbose_name = '上传意图'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = (
            '上传意图表：服务端签发的隔离区上传凭证；(user, quarantine_key) 绑定'
            '在上传初始化时确定，客户端不可改写；过期记录保留供审计'
        )
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_ui_user_status'),
            models.Index(fields=['status', 'expires_at'], name='idx_ui_status_expires'),
        ]

    def __str__(self):
        return f'上传意图#{self.pk} [{self.status}] {self.original_filename}'


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
    # P1-2D STEP 4-A 起，分片上传与其余 4 个入口走同一条链路：本字段记录的
    # intent 就是该会话的归属凭证，而上面的 oss_key **就是** intent 上的
    # quarantine key（服务端经 upload_service.begin_upload() 生成，客户端无法
    # 指定），两步 merge 出来的 KnowledgeDoc 也指向这个隔离区对象。之后只有
    # 审核给出 approved / confirmed_unchecked 且 promotion 成功，对象才会被复制到
    # knowledge/ 并允许索引。
    # 可空：历史上已存在的会话没有 intent；STEP 4-A 之前创建的会话也不会带 intent。
    # OneToOne + null=True 允许存在多个 NULL，因此对存量数据无影响；
    # on_delete=PROTECT：仍被会话引用的 intent 不允许删除，避免留下指向已删
    # 意图的孤儿会话（会话是上传行为的审计证据，宁可拒绝删除也不静默置空）。
    intent = models.OneToOneField(
        UploadIntent, on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='chunked_session', verbose_name='关联上传意图',
    )
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


class ContentModeration(models.Model):
    """内容安全审核——文档的**当前生效**审核状态（与 KnowledgeDoc 一对一）

    与 ModerationAttempt 的分工
    ---------------------------
    ContentModeration  ：当前生效状态。访问控制 / 索引门只读这一行。
    ModerationAttempt  ：不可变审核历史，只追加不更新。

    为什么不把审核字段塞进 KnowledgeDoc
    -----------------------------------
    - `KnowledgeDoc` 已有 `is_sensitive` / `sensitive_checked` 两个死字段
      （全仓库无生产写入点），再加一批审核字段会再次造成语义混乱；
    - 审核是独立生命周期（有历史、有重试、有租约），不属于文档元数据；
    - 让 `KnowledgeDoc` 的 migration 影响保持最小。

    oss_key 与 quarantine_key / promoted_key 的区别（重要）
    ------------------------------------------------------
    - `KnowledgeDoc.oss_key`：**现有语义不变**。P1-2A 不修改上传链路，
      它仍然是"文档当前关联的 OSS 对象 Key"。
    - `ContentModeration.quarantine_key`：隔离区对象（`quarantine/…`）。
    - `ContentModeration.promoted_key`：正式区对象（`knowledge/…`）。

    在 P1-2D 上传流水线完成之前，**不得**把 `KnowledgeDoc.oss_key` 的存在
    解释为"已经过审核"。审核是否通过只看本模型的 `state`。

    安全不变量
    ----------
    1. 只有 `INDEXABLE_MODERATION_STATES`（`approved` / `confirmed_unchecked`）
       允许放行。判断必须走白名单，见 `is_indexable_moderation_state()`。
    2. `unavailable` ≠ `approved`。检测链没能完成时必须停在 `unavailable`，
       绝不能因为"扫描失败"就当作干净放行。
    3. `confirmed_unchecked` 不能自动产生，只允许由
       `unavailable` + 显式人工动作产生，且必须同时具备 `confirmed_by`、
       `confirmed_at`、非空 `confirmation_reason` 三者。确认接口见
       `apps/knowledge/confirmation.py`（仅 `ADMIN_ROLES` 可调用）。

    接入现状（P1-2D STEP 4-B 已完成）
    --------------------------------
    P1-2A 只建立本模型的数据结构；到 STEP 4-B 为止三项都已接入：
    检测器（ClamAV / Rule / LLM / Vision，见 `detectors.py` + `moderation.py`）、
    上传链路创建记录（5 个入口建完 KnowledgeDoc 后都调用
    `upload_service.schedule_moderation()`）、访问门（索引门 `check_indexable()`，
    下载 / 预览门 `is_official_oss_key()`）。
    """

    # choices 唯一来源见 apps/knowledge/moderation.py
    STATE_CHOICES = MODERATION_STATE_CHOICES
    VERDICT_CHOICES = VERDICT_CHOICES
    STORAGE_STATE_CHOICES = STORAGE_STATE_CHOICES

    doc = models.OneToOneField(
        KnowledgeDoc,
        on_delete=models.CASCADE,
        related_name='moderation',
        verbose_name='关联文档',
    )
    state = models.CharField(
        max_length=24, choices=STATE_CHOICES, default=MODERATION_STATE_PENDING,
        db_index=True, verbose_name='审核状态',
    )
    verdict = models.CharField(
        max_length=24, choices=VERDICT_CHOICES, default='', blank=True,
        db_index=True, verbose_name='审核结论',
    )
    categories = models.JSONField(
        default=list, blank=True, verbose_name='结论类别列表',
    )
    findings = models.JSONField(
        default=list, blank=True,
        verbose_name='审核证据（结构约束见 moderation.MAX_FINDINGS）',
    )
    checked_at = models.DateTimeField(
        null=True, blank=True, verbose_name='本次终态判定时间',
    )
    failure_reason = models.TextField(
        blank=True, default='', verbose_name='不可用/异常原因',
    )
    service = models.CharField(
        max_length=32, blank=True, default='', verbose_name='产生结论的主要服务',
    )
    policy_version = models.CharField(
        max_length=64, blank=True, default='', verbose_name='策略版本',
    )
    applied_seq = models.PositiveIntegerField(
        default=0, verbose_name='已生效的尝试序号（CAS 用）',
    )
    storage_state = models.CharField(
        max_length=20, choices=STORAGE_STATE_CHOICES, default=STORAGE_STATE_QUARANTINE,
        db_index=True, verbose_name='存储状态',
    )
    quarantine_key = models.CharField(
        max_length=512, blank=True, default='', verbose_name='隔离区对象 Key',
    )
    promoted_key = models.CharField(
        max_length=512, blank=True, default='', verbose_name='正式区对象 Key',
    )
    promotion_error = models.TextField(
        blank=True, default='', verbose_name='提升失败原因',
    )
    promotion_retry = models.PositiveIntegerField(
        default=0, verbose_name='提升重试次数',
    )
    lease_owner = models.CharField(
        max_length=128, blank=True, default='', verbose_name='租约持有者',
    )
    lease_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name='租约到期时间',
    )
    attempt_count = models.PositiveIntegerField(
        default=0, verbose_name='审核尝试次数',
    )
    next_retry_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name='下次可重试时间',
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='confirmed_moderations',
        verbose_name='确认人',
    )
    confirmed_at = models.DateTimeField(
        null=True, blank=True, verbose_name='确认时间',
    )
    confirmation_reason = models.TextField(
        blank=True, default='', verbose_name='确认理由（人工确认放行时非空）',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'content_moderation'
        verbose_name = '内容安全审核'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = (
            '内容安全审核当前状态表(1:1 knowledge_doc;'
            'state 白名单 approved/confirmed_unchecked 才可放行;'
            'storage_state=promoted 表示对象已在 knowledge/ 正式命名空间)'
        )
        indexes = [
            # 审核门与重试扫描的联合查询路径
            models.Index(fields=['state', 'storage_state'], name='idx_cm_state_storage'),
        ]

    def __str__(self):
        return f'{self.doc_id} [{self.state}]'

    @property
    def is_approved(self) -> bool:
        """审核状态是否属于放行白名单。

        注意：**只**判断审核状态，不判断存储状态。完整的访问门（P1-2E）
        需要 `is_approved and is_promoted` 同时成立；本阶段不启用该门。
        """
        return is_indexable_moderation_state(self.state)

    @property
    def is_promoted(self) -> bool:
        """对象是否已复制到正式命名空间（`knowledge/`）。"""
        return self.storage_state == STORAGE_STATE_PROMOTED


class ModerationAttempt(models.Model):
    """审核尝试历史——**不可变**，只追加不更新

    每次 `pending → scanning` 的尝试写一行，用于回答：
    第几次审核、什么时候、哪个 worker、什么策略、哪些检测器、什么结论、为何失败。

    序号与并发
    ----------
    `(moderation, attempt_seq)` 唯一。未来的审核 worker 应用结果时必须走 CAS：

        UPDATE content_moderation
           SET state = ?, verdict = ?, applied_seq = ?
         WHERE id = ? AND applied_seq < ?

    影响行数为 0 即表示"已有更新的结果生效"，本次结果应被丢弃。
    **不要**用 `updated_at` 判断新旧——同秒精度与时钟漂移都会判错，
    单调递增的 `applied_seq` 才是可靠判据。
    """

    VERDICT_CHOICES = VERDICT_CHOICES

    moderation = models.ForeignKey(
        ContentModeration,
        on_delete=models.CASCADE,
        related_name='attempts',
        verbose_name='所属审核记录',
    )
    attempt_seq = models.PositiveIntegerField(verbose_name='尝试序号（从 1 递增）')
    state_at_start = models.CharField(
        max_length=24, choices=MODERATION_STATE_CHOICES, default=MODERATION_STATE_PENDING,
        verbose_name='开始时的审核状态',
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')
    detector_results = models.JSONField(
        default=list, blank=True, verbose_name='各检测器原始结论',
    )
    verdict = models.CharField(
        max_length=24, choices=VERDICT_CHOICES, default='', blank=True,
        verbose_name='本次结论',
    )
    error = models.TextField(blank=True, default='', verbose_name='异常信息')
    policy_version = models.CharField(
        max_length=64, blank=True, default='', verbose_name='策略版本',
    )
    worker_id = models.CharField(
        max_length=128, blank=True, default='', verbose_name='执行 worker 标识',
    )
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name='创建时间',
    )

    class Meta:
        db_table = 'moderation_attempt'
        verbose_name = '审核尝试记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '内容安全审核尝试历史表(不可变日志;unique(moderation_id, attempt_seq))'
        constraints = [
            models.UniqueConstraint(
                fields=['moderation', 'attempt_seq'],
                name='uk_attempt_moderation_seq',
            ),
        ]

    def __str__(self):
        return f'审核#{self.moderation_id} 尝试#{self.attempt_seq}'
