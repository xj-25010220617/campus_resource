from django.db import models
from django.conf import settings


class UploadLog(models.Model):
    """上传日志（关联新版 KnowledgeDoc）"""
    STATUS_CHOICES = [
        ('pending', '待处理'),
        ('success', '成功'),
        ('failed', '失败'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='upload_logs', verbose_name='上传用户'
    )
    knowledge_doc = models.ForeignKey(
        'knowledge.KnowledgeDoc', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='upload_logs',
        verbose_name='关联知识文档',
    )
    file_name = models.CharField(max_length=256, verbose_name='文件名')
    file_size = models.BigIntegerField(verbose_name='文件大小（字节）')
    file_type = models.CharField(max_length=16, verbose_name='文件类型')
    college = models.ForeignKey(
        'accounts.College', on_delete=models.PROTECT,
        null=True, blank=True,
        verbose_name='所属学院',
        db_comment='所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)；超管校级上传可为空',
    )
    department = models.ForeignKey(
        'classify.Department', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='所属部门',
        db_comment='所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)',
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='pending',
        db_index=True, verbose_name='上传状态'
    )
    error_message = models.TextField(blank=True, default='', verbose_name='错误信息')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP地址')
    user_agent = models.CharField(max_length=512, blank=True, default='', verbose_name='User-Agent')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完成时间')

    class Meta:
        db_table = 'upload_log'
        verbose_name = '上传日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        db_table_comment = '上传日志表（关联 knowledge_doc）'
        indexes = [
            models.Index(fields=['user', 'created_at'], name='idx_ulog_user_created'),
            models.Index(fields=['status', 'created_at'], name='idx_ulog_status_created'),
            models.Index(fields=['college', 'created_at'], name='idx_ulog_college_created'),
        ]

    def __str__(self):
        return f'{self.user.username} 上传 {self.file_name} [{self.status}]'
