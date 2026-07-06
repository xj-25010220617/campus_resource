from django.conf import settings
from django.db import models


class FeedbackQuestion(models.Model):
    """待人工回答的问题"""
    STATUS_CHOICES = [
        ('pending', '待回答'),
        ('answered', '已回答'),
        ('ignored', '已忽略'),
    ]

    question = models.TextField(verbose_name='问题原文')
    related_message_id = models.BigIntegerField(
        null=True, blank=True,
        verbose_name='关联AI回复消息ID',
    )
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='feedback_questions',
        verbose_name='所属学院',
        db_comment='所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)',
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='pending',
        db_index=True, verbose_name='状态',
    )
    answer_text = models.TextField(blank=True, default='', verbose_name='人工回答内容')
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='answered_feedbacks',
        verbose_name='回答人',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    answered_at = models.DateTimeField(null=True, blank=True, verbose_name='回答时间')

    class Meta:
        db_table = 'feedback_question'
        verbose_name = '待回答问题'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '待人工回答问题表'
        indexes = [
            models.Index(fields=['status', 'college'], name='idx_fq_status_college'),
        ]

    def __str__(self):
        return self.question[:50]
