"""Accounts - 用户与学院模型

- College: 二级学院（如成都东软学院13个学院）
- User: 自定义用户模型，扩展 Django AbstractUser，扩展三 → 五级角色
"""
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
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
        ('college_admin', '学院管理员'),
        ('dept_admin', '部门管理员'),
        ('admin', '管理员（兼容旧角色）'),
        ('user', '普通用户'),
    ]
    role = models.CharField(
        max_length=16, choices=ROLE_CHOICES, default='user',
        verbose_name='角色',
    )
    college = models.ForeignKey(
        College,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='所属学院',
    )
    department = models.ForeignKey(
        'classify.Department',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='所属部门',
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

    def clean(self):
        """角色与归属字段的组合校验"""
        super().clean()
        role = self.role
        if role == 'college_admin':
            if not self.college_id:
                raise ValidationError({'college': '学院管理员必须设置所属学院'})
            if self.department_id:
                raise ValidationError({'department': '学院管理员不能归属部门'})
        elif role == 'dept_admin':
            if not self.department_id:
                raise ValidationError({'department': '部门管理员必须设置所属部门'})
            if self.college_id:
                raise ValidationError({'college': '部门管理员不能归属学院'})
        elif role == 'user':
            if not self.college_id:
                raise ValidationError({'college': '学生必须设置所属学院'})
        # admin（兼容旧角色）不强制要求，保持原行为

