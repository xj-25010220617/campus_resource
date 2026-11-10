from django.db import models


class Discipline(models.Model):
    """专业/学科方向（覆盖工学/管理学/艺术学/文学/医学5大门类）"""

    LEVEL_CHOICES = [
        ('bachelor', '本科'),
        ('vocational', '高职专科'),
        ('minor', '第二学士学位'),
        ('international', '中外合作办学(本科)'),
    ]

    name = models.CharField(
        max_length=64, unique=True,
        verbose_name='专业名称',
        db_comment='专业名称(如：软件工程/计算机科学与技术)',
    )
    category = models.CharField(
        max_length=16,
        choices=[
            ('工学', '工学'),
            ('管理学', '管理学'),
            ('艺术学', '艺术学'),
            ('文学', '文学'),
            ('医学', '医学'),
        ],
        verbose_name='学科门类',
        db_comment='学科门类(工学/管理学/艺术学/文学/医学)',
    )
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    # —— 新增扩展字段 ——
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='majors',
        verbose_name='所属学院',
    )
    major_code = models.CharField(max_length=16, blank=True, default='', verbose_name='专业代码(国标)')
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default='bachelor', verbose_name='培养层次')
    duration = models.CharField(max_length=32, blank=True, default='', verbose_name='学制(如"4年"/"3年")')
    is_first_class = models.BooleanField(default=False, verbose_name='是否一流专业建设点')
    specialty_type = models.CharField(max_length=32, blank=True, default='', verbose_name='一流专业类型(国家级/省级)')
    admission_batch = models.CharField(max_length=64, blank=True, default='', verbose_name='招生批次(一本/二本等)')

    core_courses = models.TextField(blank=True, default='', verbose_name='核心课程')
    training_goal = models.TextField(blank=True, default='', verbose_name='培养目标')
    career = models.TextField(blank=True, default='', verbose_name='就业方向')
    feature_major = models.TextField(blank=True, default='', verbose_name='专业特色')

    class Meta:
        db_table = 'discipline'
        verbose_name = '专业'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        db_table_comment = '专业表(工学/管理学/艺术学/文学/医学5大门类，含所属学院)'

    def __str__(self):
        college_name = f' — {self.college.name}' if self.college else ''
        return f'{self.name}（{self.get_category_display()}{college_name}）'


class Category(models.Model):
    """资源类型（支持两级树形分类）"""
    name = models.CharField(
        max_length=64,
        verbose_name='类型名称',
        db_comment='类型名称(id:1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)',
    )
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='children',
        verbose_name='上级分类',
        db_comment='上级分类(id:1=教学课件>PPT课件,2=试题库>期末考试...)',
    )
    icon = models.URLField(max_length=512, blank=True, default='', verbose_name='分类图标URL')
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'category'
        verbose_name = '资源类型'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        db_table_comment = '资源类型表(1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)'

    def __str__(self):
        if self.parent:
            return f'{self.parent.name} > {self.name}'
        return self.name


class Department(models.Model):
    """部门（两级树形结构）"""
    name = models.CharField(max_length=128, verbose_name='部门名称')
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='children', verbose_name='上级部门'
    )
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'department'
        verbose_name = '部门'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']
        db_table_comment = '部门表(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育与国际教育)'

    def __str__(self):
        if self.parent:
            return f'{self.parent.name} > {self.name}'
        return self.name
