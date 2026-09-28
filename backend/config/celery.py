"""
Celery 异步任务配置
- 文档索引任务：统一在 apps.knowledge.tasks.index_document_task（上传后自动触发，核心链路）
  本模块仅保留历史任务名 campus_knowledge.index_document 的转发入口
- 定时同步任务（二期：官网爬虫、图书馆摘要）
- FAQ 自动生成（每周一 03:00）
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
    # P1-2D STEP 4-B：审核异常（error）文档的重试调度。
    # 2026-09-28 起 unavailable 不再重试（它已是终态且自动入库），本调度只挑
    # state=error、next_retry_at 已到期且 attempt_count < MAX_ATTEMPTS 的记录，
    # 达到上限的文档保持原状态，绝不自动放行。10 分钟一轮与默认退避
    # （CONTENT_MODERATION_RETRY_BACKOFF_SECONDS=300s）同量级，属低频轮询。
    'retry-failed-moderations': {
        'task': 'apps.knowledge.tasks.retry_failed_moderations_task',
        'schedule': crontab(minute='*/10'),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Celery 调试任务"""
    logger.info(f'Celery debug task: {self.request.id}')


# ------------------------------------------------------------------
# 文档异步索引任务（兼容保留）
#
# 索引实现已统一到 apps.knowledge.tasks.index_document_task，该任务内部会调用
# apps.knowledge.moderation.check_indexable 做集中式的索引前置安全校验。
#
# 本任务名 `campus_knowledge.index_document` 是历史遗留入口，虽然仓库内已无
# 调用方，但它注册在 Celery app 上——broker 中一条残留消息即可执行它，从而
# 绕开统一安全路径。因此这里只做转发，不再保留第二套索引实现。
#
# 旧实现的额外问题（转发后一并消除）：会写入 'processing' / 'error' 两个
# **不在 KnowledgeDoc.STATUS_CHOICES 中**的状态值。
# ------------------------------------------------------------------
@app.task(bind=True, ignore_result=True)
def index_document(self, doc_id: int):
    """[兼容保留] 历史任务名，转发至 apps.knowledge.tasks.index_document_task。

    Args:
        doc_id: knowledge_doc 主键
    """
    from apps.knowledge.tasks import index_document_task
    from utils.celery_dispatcher import safe_delay

    logger.info('campus_knowledge.index_document 已废弃，转发至 index_document_task doc_id=%s', doc_id)
    # 走 safe_delay 而非 task.run()：本地无 Celery 时会自动降级为同步执行，
    # 两种环境下都落到同一个任务体上。
    return safe_delay(index_document_task, doc_id)


# ------------------------------------------------------------------
# 文档解析 / 分块（内部）
#
# 原先此处存在一整套与 apps/knowledge/extractor.py 重复的解析器实现
# （_parse_document / _parse_pdf / _parse_docx / _parse_pptx / _parse_xlsx /
#  _chunk_text），仅供上面那个已废弃的索引任务使用。索引实现统一后它们
# 不再被任何代码引用，已删除，避免两套解析规则各自漂移。
# ------------------------------------------------------------------
