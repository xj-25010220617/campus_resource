from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_department'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('super_admin', '超级管理员'),
                    ('college_admin', '学院管理员'),
                    ('dept_admin', '部门管理员'),
                    ('admin', '管理员（兼容旧角色）'),
                    ('user', '普通用户'),
                ],
                default='user',
                max_length=16,
                verbose_name='角色',
            ),
        ),
    ]