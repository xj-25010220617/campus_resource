"""Accounts - 用户与学院模型

- College: 二级学院（如成都东软学院13个学院），增强了简介/院长/特色等字段
- SchoolProfile: 学校概况单例（校训/校长/地址/办学规模等）
- Scholarship: 奖助学金条目
- Tuition: 学费标准条目
- AdmissionRule: 录取规则（按年份）
- AcademicCalendar: 学期/假期安排
- User: 自定义用户模型，扩展 Django AbstractUser，扩展三 → 五级角色
"""
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


# ---------------------------------------------------------------------------
# 学校概况（单例）
# ---------------------------------------------------------------------------
class SchoolProfile(models.Model):
    """学校概况 — 单例模型（全表仅一条记录）"""

    # —— 基础身份 ——
    name_full = models.CharField(max_length=128, verbose_name='学校全称', default='成都东软学院')
    name_en = models.CharField(max_length=128, blank=True, default='', verbose_name='英文名称')
    motto = models.CharField(max_length=64, blank=True, default='', verbose_name='校训')
    president = models.CharField(max_length=64, blank=True, default='', verbose_name='校长')
    party_secretary = models.CharField(max_length=64, blank=True, default='', verbose_name='党委书记')

    # —— 联系方式 ——
    address = models.CharField(max_length=256, blank=True, default='', verbose_name='地址')
    postal_code = models.CharField(max_length=16, blank=True, default='', verbose_name='邮编')
    website = models.URLField(max_length=256, blank=True, default='', verbose_name='官网')
    admission_website = models.URLField(max_length=256, blank=True, default='', verbose_name='招生网')
    admission_phone = models.CharField(max_length=128, blank=True, default='', verbose_name='招生电话')
    admission_email = models.EmailField(blank=True, default='', verbose_name='招生邮箱')

    # —— 办学资质 ——
    school_code = models.CharField(max_length=32, blank=True, default='', verbose_name='学校标识码')
    admission_code_sichuan = models.CharField(max_length=16, blank=True, default='', verbose_name='四川省招生代码')
    nature = models.CharField(max_length=32, blank=True, default='', verbose_name='办学性质')
    level = models.CharField(max_length=64, blank=True, default='', verbose_name='办学层次')
    disciplines_text = models.CharField(max_length=128, blank=True, default='', verbose_name='学科门类')

    # —— 时间节点 ——
    founded_at = models.IntegerField(null=True, blank=True, verbose_name='建校年份')
    upgraded_to_bachelor = models.IntegerField(null=True, blank=True, verbose_name='升格本科年份')

    # —— 办学规模 ——
    area_m2 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='占地面积(万平方米)')
    building_area_m2 = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='建筑面积(万平方米)')
    student_count = models.IntegerField(null=True, blank=True, verbose_name='在校生数')
    graduate_total = models.IntegerField(null=True, blank=True, verbose_name='累计毕业生数')
    major_count = models.IntegerField(null=True, blank=True, verbose_name='本科专业数')
    vocational_major_count = models.IntegerField(null=True, blank=True, verbose_name='高职专科专业数')

    # —— 师资 ——
    teacher_count = models.IntegerField(null=True, blank=True, verbose_name='教职工数')
    fulltime_teacher_count = models.IntegerField(null=True, blank=True, verbose_name='专任教师数')
    external_teacher_count = models.IntegerField(null=True, blank=True, verbose_name='外聘教师数')
    double_qualified_count = models.IntegerField(null=True, blank=True, verbose_name='"双师型"教师数')
    senior_title_count = models.IntegerField(null=True, blank=True, verbose_name='高级职称教师数')
    phd_master_count = models.IntegerField(null=True, blank=True, verbose_name='硕博学位教师数')
    phd_master_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='硕博教师占比(%)')

    # —— 培养与合作 ——
    employment_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='本科毕业生就业率(%)')
    training_model = models.TextField(blank=True, default='', verbose_name='人才培养模式')
    semester_system = models.TextField(blank=True, default='', verbose_name='学期制度')
    partner_text = models.TextField(blank=True, default='', verbose_name='合作企业')
    history = models.TextField(blank=True, default='', verbose_name='历史沿革')
    mission = models.TextField(blank=True, default='', verbose_name='办学定位')

    # —— 校领导班子（完整文本快照，便于 RAG 拼接）——
    leaders_text = models.TextField(blank=True, default='', verbose_name='校领导班子(文本快照)')

    # —— 管理 ——
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'school_profile'
        verbose_name = '学校概况'
        verbose_name_plural = verbose_name

    def save(self, *args, **kwargs):
        # 强制单例：如果已有记录，继续保存那条
        if not self.pk and SchoolProfile.objects.exists():
            self.pk = SchoolProfile.objects.first().pk
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name_full


# ---------------------------------------------------------------------------
# 二级学院（增强）
# ---------------------------------------------------------------------------
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

    # —— 新增扩展字段 ——
    intro = models.TextField(blank=True, default='', verbose_name='学院简介')
    dean = models.CharField(max_length=64, blank=True, default='', verbose_name='院长')
    secretary = models.CharField(max_length=64, blank=True, default='', verbose_name='党委书记/党总支书记')
    website = models.URLField(max_length=256, blank=True, default='', verbose_name='学院官网')
    founded_at = models.IntegerField(null=True, blank=True, verbose_name='成立/升格年份')
    features = models.TextField(blank=True, default='', verbose_name='特色亮点')
    partners = models.TextField(blank=True, default='', verbose_name='合作企业/机构')
    specialties_text = models.TextField(blank=True, default='', verbose_name='专业列表(文本快照)')

    class Meta:
        db_table = 'college'
        verbose_name = '学院'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        db_table_comment = '二级学院表(id:1=计算机与软件,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)'

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# 奖助学金
# ---------------------------------------------------------------------------
class Scholarship(models.Model):
    """奖助学金条目"""
    TYPE_CHOICES = [
        ('scholarship', '奖学金'),
        ('grant', '助学金'),
        ('loan', '助学贷款'),
    ]
    LEVEL_CHOICES = [
        ('national', '国家级'),
        ('provincial', '省级'),
        ('university', '校级'),
        ('enterprise', '企业设立'),
    ]

    name = models.CharField(max_length=128, verbose_name='名称')
    type = models.CharField(max_length=16, choices=TYPE_CHOICES, default='scholarship', verbose_name='类型')
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default='university', verbose_name='级别')
    amount = models.CharField(max_length=128, blank=True, default='', verbose_name='金额/额度描述')
    description = models.TextField(blank=True, default='', verbose_name='申请条件与说明')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'scholarship'
        verbose_name = '奖助学金'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.get_level_display()} · {self.name}'


# ---------------------------------------------------------------------------
# 学费标准
# ---------------------------------------------------------------------------
class Tuition(models.Model):
    """学费标准条目"""
    category = models.CharField(max_length=64, unique=True, verbose_name='类别(如"普通本科"/"中外合作办学")')
    amount_text = models.CharField(max_length=128, blank=True, default='', verbose_name='金额描述(如"18000～20000元/年")')
    description = models.TextField(blank=True, default='', verbose_name='说明')

    class Meta:
        db_table = 'tuition'
        verbose_name = '学费标准'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.category


# ---------------------------------------------------------------------------
# 录取规则
# ---------------------------------------------------------------------------
class AdmissionRule(models.Model):
    """录取规则（按年份）"""
    year = models.IntegerField(unique=True, verbose_name='适用年份')
    content = models.TextField(blank=True, default='', verbose_name='录取规则正文')
    exam_requirement = models.TextField(blank=True, default='', verbose_name='体检/考核要求')
    transfer_policy = models.TextField(blank=True, default='', verbose_name='转专业政策')

    class Meta:
        db_table = 'admission_rule'
        verbose_name = '录取规则'
        verbose_name_plural = verbose_name
        ordering = ['-year']

    def __str__(self):
        return f'{self.year}年录取规则'


# ---------------------------------------------------------------------------
# 学期/校历
# ---------------------------------------------------------------------------
class AcademicCalendar(models.Model):
    """学期与假期安排"""
    TERM_TYPE_CHOICES = [
        ('theory', '理论学期'),
        ('practice', '实践学期'),
        ('summer', '暑假'),
        ('winter', '寒假'),
        ('military', '军训'),
        ('graduation', '毕业典礼'),
    ]

    term_name = models.CharField(max_length=128, verbose_name='名称(如"2025-2026学年第一学期")')
    term_type = models.CharField(max_length=16, choices=TERM_TYPE_CHOICES, default='theory', verbose_name='类型')
    start_date = models.DateField(null=True, blank=True, verbose_name='开始日期')
    end_date = models.DateField(null=True, blank=True, verbose_name='结束日期')
    note = models.CharField(max_length=256, blank=True, default='', verbose_name='备注')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'academic_calendar'
        verbose_name = '学期校历'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'start_date']

    def __str__(self):
        return self.term_name


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
