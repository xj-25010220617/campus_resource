"""Accounts - 用户与学院模型

- College: 二级学院（如成都东软学院13个学院）
- User: 自定义用户模型，扩展 Django AbstractUser，扩展三 → 五级角色
"""
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class College(models.Model):
    """二级学院（成都东软学院共13个）"""
    name = models.CharField(
        max_length=64, unique=True,
        verbose_name='学院名称',
        db_comment='学院名称(id:1=计算机与软件,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)',
    )
    code = models.CharField(
        max_length=16, unique=True,
        verbose_name='学院代码',
        db_comment='学院代码(CS/ISE/IBM/DAD/FL/HMT/HVC/IEC/BED/MAR/IEE/CED/VET)',
    )
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'college'
        verbose_name = '学院'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        db_table_comment = '二级学院表(id:1=计算机与软件,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)'

    def __str__(self):
        return self.name


class User(AbstractUser):
    """自定义用户模型"""
    ROLE_CHOICES = [
        ('super_admin', '超级管理员'),
        ('college_admin', '学院管理员'),
        ('dept_admin', '部门管理员'),
        ('reviewer', '审核员'),
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
        db_comment='所属学院(id:1=计算机与软件,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)',
    )
    department = models.ForeignKey(
        'classify.Department',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='所属部门',
        db_comment='所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)',
    )
    phone = models.CharField(max_length=11, blank=True, default='', verbose_name='手机号')
    avatar = models.URLField(blank=True, default='', verbose_name='头像URL')
    nickname = models.CharField(max_length=64, blank=True, default='', verbose_name='昵称')
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
        elif role == 'reviewer':
            # 审核员：归属二选一——绑部门=审本部门文档，绑学院=审本学院「学院级」文档
            if bool(self.department_id) == bool(self.college_id):
                raise ValidationError({
                    'role': '审核员必须在「所属部门」和「所属学院」之间二选一',
                })
        elif role == 'user':
            if not self.college_id:
                raise ValidationError({'college': '学生必须设置所属学院'})
        # admin（兼容旧角色）不强制要求，保持原行为
