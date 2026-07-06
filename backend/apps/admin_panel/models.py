"""Admin Panel - 超管审计日志模型

- LoginLog: 登录尝试（JWT / SSO / SSO Mock）审计
- OperationLog: 超管 CRUD（user/resource/category）审计

注意：两张表由远程数据库手动创建（见 database-changes/），
本模块使用 `managed=False`，避免 Django makemigrations 自动建表。
"""
from django.conf import settings
from django.db import models


class LoginLog(models.Model):
    """管理后台登录行为日志（成功 / 失败）"""

    LOGIN_TYPE_CHOICES = [
        ('jwt', '账号密码登录'),
        ('sso', 'SSO 登录'),
        ('sso_mock', 'SSO Mock 登录'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        null=True, blank=True,
        db_column='user_id',
        related_name='admin_login_logs',
        verbose_name='登录用户',
    )
    username = models.CharField(max_length=128, verbose_name='登录用户名（快照）')
    login_type = models.CharField(
        max_length=16,
        choices=LOGIN_TYPE_CHOICES,
        db_index=True,
        verbose_name='登录方式',
    )
    success = models.BooleanField(default=False, db_index=True, verbose_name='是否成功')
    fail_reason = models.CharField(
        max_length=256,
        blank=True, default='',
        verbose_name='失败原因',
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name='IP',
    )
    user_agent = models.CharField(
        max_length=512,
        blank=True, default='',
        verbose_name='User-Agent',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='登录时间',
    )

    class Meta:
        managed = False
        db_table = 'admin_login_log'
        verbose_name = '管理登录日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        status = '成功' if self.success else '失败'
        return f'{self.username} {self.login_type} {status}'


class OperationLog(models.Model):
    """管理后台超管 CRUD 操作日志（用户 / 资源 / 资源分类）"""

    ACTION_CHOICES = [
        ('create', '新建'),
        ('update', '编辑'),
        ('delete', '删除'),
        ('reset_password', '重置密码'),
        ('batch_delete', '批量删除'),
        ('batch_reset_password', '批量重置密码'),
    ]
    TARGET_CHOICES = [
        ('user', '用户'),
        ('resource', '资源'),
        ('category', '资源分类'),
    ]

    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        null=True, blank=True,
        db_column='operator_id',
        related_name='admin_operation_logs',
        verbose_name='操作人',
    )
    operator_username = models.CharField(
        max_length=128,
        verbose_name='操作人（快照）',
    )
    action = models.CharField(
        max_length=32,
        choices=ACTION_CHOICES,
        db_index=True,
        verbose_name='动作',
    )
    target_type = models.CharField(
        max_length=32,
        choices=TARGET_CHOICES,
        db_index=True,
        verbose_name='对象类型',
    )
    target_id = models.BigIntegerField(
        null=True, blank=True, db_index=True, verbose_name='对象ID',
    )
    target_repr = models.CharField(
        max_length=256,
        blank=True, default='',
        verbose_name='对象摘要',
    )
    after = models.JSONField(
        null=True, blank=True,
        verbose_name='操作后数据快照',
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name='IP',
    )
    user_agent = models.CharField(
        max_length=512,
        blank=True, default='',
        verbose_name='User-Agent',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='操作时间',
    )

    class Meta:
        managed = False
        db_table = 'admin_operation_log'
        verbose_name = '管理操作日志'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.operator_username} {self.action} {self.target_type}#{self.target_id}'
