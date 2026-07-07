from django.conf import settings
from django.db import models


class FAQCategory(models.Model):
    """FAQ 分类"""
    name = models.CharField(max_length=100, verbose_name='分类名称')
    icon = models.CharField(max_length=50, blank=True, default='', verbose_name='图标')
    sort_order = models.IntegerField(default=0, verbose_name='排序')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        db_table = 'faq_category'
        verbose_name = 'FAQ 分类'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class FAQItem(models.Model):
    """FAQ 条目"""
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('published', '已发布'),
        ('rejected', '已驳回'),
    ]

    question = models.CharField(max_length=500, verbose_name='标准化问题')
    answer = models.TextField(verbose_name='标准答案')
    category = models.ForeignKey(
        FAQCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='faq_items',
        verbose_name='所属分类',
    )
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='faq_items',
        verbose_name='所属学院',
        db_comment='所属学院(NULL=全局; 1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)',
    )
    tags = models.JSONField(default=list, blank=True, verbose_name='关键词标签')
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft',
        db_index=True, verbose_name='状态',
    )
    frequency = models.FloatField(default=0.0, verbose_name='该问题占比(%)')
    variant_count = models.IntegerField(default=0, verbose_name='相似变体合并数')
    source_period_start = models.DateTimeField(null=True, blank=True, verbose_name='分析周期起始')
    source_period_end = models.DateTimeField(null=True, blank=True, verbose_name='分析周期结束')
    related_doc_ids = models.JSONField(default=list, blank=True, verbose_name='关联知识库文档ID')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_faqs',
        verbose_name='审核人',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='审核时间')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'faq_item'
        verbose_name = 'FAQ 条目'
        verbose_name_plural = verbose_name
        ordering = ['-frequency', '-created_at']
        db_table_comment = 'FAQ条目表(NULL=全局; 1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)'
        indexes = [
            models.Index(fields=['status', 'college'], name='idx_faq_status_college'),
        ]

    def __str__(self):
        return self.question[:50]


class FAQGenerationLog(models.Model):
    """FAQ 生成日志"""
    STATUS_CHOICES = [
        ('running', '运行中'),
        ('completed', '已完成'),
        ('failed', '失败'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='running', verbose_name='状态')
    total_questions = models.IntegerField(default=0, verbose_name='采集问题数')
    clusters_found = models.IntegerField(default=0, verbose_name='聚类簇数')
    drafts_generated = models.IntegerField(default=0, verbose_name='生成草稿数')
    error_message = models.TextField(blank=True, default='', verbose_name='错误信息')
    started_at = models.DateTimeField(auto_now_add=True, verbose_name='开始时间')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完成时间')

    class Meta:
        db_table = 'faq_generation_log'
        verbose_name = 'FAQ 生成日志'
        verbose_name_plural = verbose_name
        ordering = ['-started_at']

    def __str__(self):
        return f'FAQ生成 #{self.id} [{self.status}]'
