from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0001_initial'),
        ('classify', '0003_add_department'),
        ('knowledge', '0011_knowledgedoc_review_comment_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='KnowledgeFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=64, verbose_name='目录名称')),
                ('is_system', models.BooleanField(default=False, verbose_name='系统自动分类')),
                ('system_key', models.CharField(blank=True, default='', max_length=64, verbose_name='系统分类标识')),
                ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('college', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='knowledge_folders', to='accounts.college', verbose_name='所属学院')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='knowledge_folders', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='knowledge_folders', to='classify.department', verbose_name='所属部门')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='knowledge.knowledgefolder', verbose_name='上级目录')),
            ],
            options={
                'verbose_name': '资料目录',
                'verbose_name_plural': '资料目录',
                'db_table': 'knowledge_folder',
                'ordering': ['sort_order', 'id'],
                'db_table_comment': '部门资料目录表(按college_id/department_id隔离，is_system=AI自动分类目录)',
                'indexes': [
                    models.Index(fields=['department', 'parent'], name='idx_folder_dept_parent'),
                    models.Index(fields=['college', 'parent'], name='idx_folder_college_parent'),
                ],
            },
        ),
        migrations.AddField(
            model_name='knowledgedoc',
            name='folder',
            field=models.ForeignKey(blank=True, db_comment='部门资料目录；为空表示待分类', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='docs', to='knowledge.knowledgefolder', verbose_name='所属目录'),
        ),
    ]
