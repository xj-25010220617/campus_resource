# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='rating',
            field=models.CharField(
                blank=True,
                choices=[('like', '点赞'), ('dislike', '点踩')],
                max_length=10,
                null=True,
                verbose_name='用户反馈'
            ),
        ),
    ]
