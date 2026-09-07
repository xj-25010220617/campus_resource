from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('knowledge', '0006_chunkeduploadsession_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='knowledgedoc',
            name='index_error',
            field=models.TextField(blank=True, default='', verbose_name='索引错误信息'),
        ),
        migrations.AddField(
            model_name='knowledgedoc',
            name='index_attempts',
            field=models.PositiveIntegerField(default=0, verbose_name='索引尝试次数'),
        ),
        migrations.AddField(
            model_name='knowledgedoc',
            name='index_started_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='索引开始时间'),
        ),
        migrations.AddField(
            model_name='knowledgedoc',
            name='failed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最终失败时间'),
        ),
    ]
