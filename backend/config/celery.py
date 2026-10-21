"""
Celery 异步任务配置
- 定时同步任务（官网爬虫、每日上下文刷新等）
- FAQ 自动生成（每周一 03:00）

文档索引任务在 apps.knowledge.tasks.index_document_task。
"""
import logging
import os

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger(__name__)

# Django settings 作为 Celery 配置源
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('campus_knowledge')

# 从 Django settings 读取配置，namespace='CELERY'
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现各 app 下的 tasks.py
app.autodiscover_tasks()


# ------------------------------------------------------------------
# 定时任务配置
# ------------------------------------------------------------------
app.conf.beat_schedule = {
    'generate-faq-weekly': {
        'task': 'apps.faq.tasks.generate_faq_weekly',
        'schedule': crontab(hour=3, minute=0, day_of_week=1),  # 每周一 03:00
    },
    'crawl-neusoft-website-daily': {
        'task': 'apps.knowledge.tasks.crawl_neusoft_website_task',
        'schedule': crontab(hour=4, minute=0),  # 每天 04:00
        'kwargs': {'max_pages': 100, 'uploader_id': 1, 'download_files': True},
    },
    'crawl-neusoft-website-weekly-full': {
        'task': 'apps.knowledge.tasks.crawl_neusoft_website_task',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),  # 每周日 05:00
        'kwargs': {'max_pages': 300, 'uploader_id': 1, 'download_files': True},
    },
    'refresh-daily-context': {
        'task': 'apps.knowledge.tasks.refresh_daily_context_task',
        'schedule': crontab(hour=0, minute=5),  # 每天 00:05
    },
    'cleanup-expired-upload-sessions': {
        'task': 'apps.knowledge.tasks.cleanup_expired_upload_sessions_task',
        'schedule': crontab(hour=3, minute=30),  # 每天 03:30
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Celery 调试任务"""
    logger.info(f'Celery debug task: {self.request.id}')
