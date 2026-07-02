"""
知识库异步任务
- extract_freshness_task：上传完成后异步提取发布日期

设计要点：
- 上传主链路不阻塞；Celery 失败重试 2 次
- 失败时 doc.freshness_date 留空，前端可手动触发重试
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def extract_freshness_task(self, doc_id: int):
    """
    异步提取文档发布日期。

    Args:
        doc_id: KnowledgeDoc 主键

    Returns:
        None（结果已直接写回数据库）

    异常处理:
        - 任意异常 → 自动重试 2 次（间隔 30s）
        - 最终失败仅记录日志，不影响上传主流程
    """
    from apps.knowledge.extractor import extract_freshness_for_doc

    try:
        result = extract_freshness_for_doc(doc_id)
        if result is None:
            logger.warning(f'extract_freshness_task: doc_id={doc_id} 不存在')
        return None
    except Exception as e:
        logger.error(f'extract_freshness_task doc_id={doc_id} 失败: {e}')
        # 重试：让 Celery worker 重跑
        raise self.retry(exc=e)
