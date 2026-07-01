from django.db import models


class Discipline(models.Model):
    """学科方向（覆盖工学/管理学/艺术学/文学/医学5大门类）"""
    DISCIPLINE_CATEGORIES = [
        ('工学', '工学'),
        ('管理学', '管理学'),
        ('艺术学', '艺术学'),
        ('文学', '文学'),
        ('医学', '医学'),
    ]
    name = models.CharField(max_length=64, unique=True, verbose_name='学科方向名称')
    category = models.CharField(
        max_length=16, choices=DISCIPLINE_CATEGORIES, verbose_name='学科门类'
    )
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'discipline'
        verbose_name = '学科方向'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.name}（{self.category}）'


class Category(models.Model):
    """资源类型（支持两级：课件 → PPT课件/PDF课件）"""
    name = models.CharField(max_length=64, verbose_name='类型名称')
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='children', verbose_name='上级分类'
    )
    sort_order = models.IntegerField(default=0, verbose_name='排序')

    class Meta:
        db_table = 'category'
        verbose_name = '资源类型'
        verbose_name_plural = verbose_name
        ordering = ['sort_order', 'id']

    def __str__(self):
        if self.parent:
            return f'{self.parent.name} > {self.name}'
        return self.name
