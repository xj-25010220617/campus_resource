"""
Celery 异步任务配置
- 文档索引任务模板（上传后自动触发）
- 定时同步任务（二期：官网爬虫、图书馆摘要）
"""
import os
from celery import Celery

# Django settings 作为 Celery 配置源
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('campus_knowledge')

# 从 Django settings 读取配置，namespace='CELERY'
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现各 app 下的 tasks.py
app.autodiscover_tasks()


# ------------------------------------------------------------------
# 定时任务配置（在 settings.py 中添加 CELERY_BEAT_SCHEDULE）
# ------------------------------------------------------------------
# CELERY_BEAT_SCHEDULE = {
#     'sync-website-daily': {
#         'task': 'tasks.sync_website.sync_website_task',
#         'schedule': crontab(hour=3, minute=0),  # 每天凌晨3点
#     },
# }


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Celery 调试任务"""
    print(f'Celery debug task: {self.request.id}')
