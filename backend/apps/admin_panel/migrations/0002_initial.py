"""admin_panel 模型注册迁移（managed=False，不建表）。

背景：admin_panel/models.py 的 LoginLog / OperationLog 两张表使用
`managed = False`（表由 0001_initial 的 RunSQL 幂等创建），Django 默认不会为其生成
任何 migration。`makemigrations` 每次都抱怨 "changes not yet reflected in a migration"，
实际是一个永远无法自然消解的警告。

本迁移纯占位：只 CreateModel 注册元信息（db_table 与 managed=False），让 migrate 记录
这两张表已纳入迁移体系；任何反向迁移都不会删表（DROP 由 0001 的 reverse 负责）。
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('admin_panel', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(max_length=128, verbose_name='登录用户名（快照）')),
                ('login_type', models.CharField(choices=[('jwt', '账号密码登录'), ('sso', 'SSO 登录'), ('sso_mock', 'SSO Mock 登录')], db_index=True, max_length=16, verbose_name='登录方式')),
                ('success', models.BooleanField(db_index=True, default=False, verbose_name='是否成功')),
                ('fail_reason', models.CharField(blank=True, default='', max_length=256, verbose_name='失败原因')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP')),
                ('user_agent', models.CharField(blank=True, default='', max_length=512, verbose_name='User-Agent')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='登录时间')),
            ],
            options={
                'verbose_name': '管理登录日志',
                'verbose_name_plural': '管理登录日志',
                'db_table': 'admin_login_log',
                'ordering': ['-created_at'],
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='OperationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operator_username', models.CharField(max_length=128, verbose_name='操作人（快照）')),
                ('action', models.CharField(choices=[('create', '新建'), ('update', '编辑'), ('delete', '删除'), ('reset_password', '重置密码'), ('batch_delete', '批量删除'), ('batch_reset_password', '批量重置密码')], db_index=True, max_length=32, verbose_name='动作')),
                ('target_type', models.CharField(choices=[('user', '用户'), ('resource', '资源'), ('category', '资源分类'), ('department', '部门')], db_index=True, max_length=32, verbose_name='对象类型')),
                ('target_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='对象ID')),
                ('target_repr', models.CharField(blank=True, default='', max_length=256, verbose_name='对象摘要')),
                ('after', models.JSONField(blank=True, null=True, verbose_name='操作后数据快照')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP')),
                ('user_agent', models.CharField(blank=True, default='', max_length=512, verbose_name='User-Agent')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='操作时间')),
            ],
            options={
                'verbose_name': '管理操作日志',
                'verbose_name_plural': '管理操作日志',
                'db_table': 'admin_operation_log',
                'ordering': ['-created_at'],
                'managed': False,
            },
        ),
    ]
