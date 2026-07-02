from django.contrib.auth.models import AbstractUser
from django.db import models


class College(models.Model):
    """二级学院（成都东软学院共13个）"""
    name = models.CharField(max_length=64, unique=True, verbose_name='学院名称')
    code = models.CharField(max_length=16, unique=True, verbose_name='学院代码')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'college'
        verbose_name = '学院'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class User(AbstractUser):
    """自定义用户模型"""
    ROLE_CHOICES = [
        ('super_admin', '超级管理员'),
        ('admin', '管理员'),
        ('user', '普通用户'),
    ]
    role = models.CharField(
        max_length=16, choices=ROLE_CHOICES, default='user',
        verbose_name='角色'
    )
    college = models.ForeignKey(
        College, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='所属学院'
    )
    phone = models.CharField(max_length=11, blank=True, default='', verbose_name='手机号')
    avatar = models.URLField(blank=True, default='', verbose_name='头像URL')
    sso_uid = models.CharField(
        max_length=128, null=True, blank=True, unique=True,
        db_index=True, verbose_name='SSO统一ID',
    )

    class Meta:
        db_table = 'user'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.username}（{self.get_role_display()}）'
