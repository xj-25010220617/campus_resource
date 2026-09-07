"""
知识库异步任务
- extract_freshness_task：上传完成后异步提取发布日期
- index_document_task：上传完成后异步索引入库（分块 → Embedding → Milvus）
- crawl_neusoft_website_task：定时爬取官网

设计要点：
- 上传主链路不阻塞；Celery 失败重试 2 次
- 失败时 doc.freshness_date 留空，前端可手动触发重试
- 索引失败时 doc.status 保持 'failed'，可手动重试
- Celery 未安装时（本地开发）自动降级为同步执行，视图层不需要写 try/except
"""
import logging
import re
from typing import Optional

from utils.shared_task_compat import shared_task

logger = logging.getLogger(__name__)


def _cleanse_for_mysql_utf8mb3(text: str) -> str:
    """清除 4 字节 Unicode 字符（MySQL utf8mb3 无法存储的码点）。"""
    if not text:
        return text
    # 码点 >= U+10000 需要 4 字节 UTF-8 编码，utf8mb3 不支持
    # Python 中这些字符的 ord() >= 0x10000
    result = []
    for c in text:
        cp = ord(c)
        if cp < 0x10000:
            result.append(c)
    return ''.join(result)


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True)
def extract_freshness_task(self, doc_id: int):
    """异步提取文档发布日期。

    Args:
        doc_id: KnowledgeDoc 主键

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
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=120, ignore_result=True)
def crawl_neusoft_website_task(
    self, max_pages: int = 100,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
):
    """
    爬取成都东软学院官网并索引到知识库。

    Args:
        max_pages: 本次爬取最大页数
        uploader_id: 上传者用户 ID
        college_id: 所属学院 ID（默认 None=校级公开）
        discipline_id: 学科方向 ID（可选，默认自动推断）
    """
    from utils.crawler import crawl_and_index

    try:
        saved = crawl_and_index(
            max_pages=max_pages,
            uploader_id=uploader_id,
            college_id=college_id,
            discipline_id=discipline_id,
        )
        logger.info(f'官网爬取任务完成: 保存 {saved} 篇文档')
        return saved
    except Exception as e:
        logger.error(f'官网爬取任务失败: {e}')
        raise self.retry(exc=e)


@shared_task(bind=True, ignore_result=True)
def refresh_daily_context_task(self):
    """每日 00:05 心跳：刷新"每日数据"上下文，记录日期切换。

    实际刷新逻辑在各实现中（课表/通知/天气等）；
    本任务主要作用：
    1. 调用 utils.context 打印/记录当前日期，作为跨天信号
    2. 预热今日数据缓存（如果后续加了缓存）
    3. 调度其他每日子任务（未来扩展点）
    """
    from django.utils import timezone
    from utils.context import get_current_time_str, get_daily_briefing
    now = timezone.now()
    time_str = get_current_time_str(now=now)
    briefing = get_daily_briefing()
    logger.info('[每日上下文刷新] %s | briefing_chars=%d', time_str, len(briefing))
    return {'time': time_str, 'briefing_chars': len(briefing)}


@shared_task(bind=True, max_retries=2, default_retry_delay=60, ignore_result=True)
def index_document_task(self, doc_id: int):
    """文档索引入库：解析 → 滑动窗口分块 → Embedding → Milvus + MySQL。

    Args:
        doc_id: KnowledgeDoc 主键

    流程：
        1. 从 OSS 下载并提取文本
        2. 滑动窗口分块（500字/块，100字重叠）
        3. 调用 DeepSeek Embedding API 向量化
        4. 写入 Milvus 向量库
        5. 写入 MySQL DocChunk 表（用于引用溯源）
        6. 更新 KnowledgeDoc 状态为 'indexed'

    异常处理:
        - 任意异常 → 自动重试 2 次
        - 最终失败 → doc.status = 'failed'
    """
    from django.utils import timezone

    from apps.knowledge.extractor import extract_text_from_oss
    from apps.knowledge.models import DocChunk, KnowledgeDoc
    from apps.knowledge.utils import chunk_document
    from utils.llm import llm_client
    from utils.milvus import milvus_client

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'index_document_task: doc_id={doc_id} 不存在')
        return None

    # Celery 绑定任务提供 request.retries；本地同步降级时使用 0 次重试作为默认值。
    task_request = getattr(self, 'request', None)
    retries = getattr(task_request, 'retries', 0)
    max_retries = getattr(self, 'max_retries', 2) if self is not None else 0

    try:
        # 1. 解析：提取文本
        logger.info(f'开始索引 doc_id={doc_id} title="{doc.title}"')
        doc.status = 'parsing'
        doc.index_attempts = retries + 1
        doc.index_started_at = timezone.now()
        doc.index_error = ''
        doc.failed_at = None
        doc.save(update_fields=[
            'status', 'index_attempts', 'index_started_at', 'index_error',
            'failed_at', 'updated_at',
        ])

        text = extract_text_from_oss(doc.oss_key, doc.file_type, doc.file_name)
        if not text or not text.strip():
            logger.warning(f'doc_id={doc_id} 文本提取为空，跳过索引')
            doc.status = 'failed'
            doc.index_error = '解析结果为空：未提取到可检索文本'
            doc.failed_at = timezone.now()
            doc.save(update_fields=['status', 'index_error', 'failed_at', 'updated_at'])
            return None

        # 2. 分块
        doc.status = 'chunking'
        doc.save(update_fields=['status', 'updated_at'])

        raw_chunks = chunk_document(text, chunk_size=500, overlap=100)
        if not raw_chunks:
            logger.warning(f'doc_id={doc_id} 分块结果为空，跳过索引')
            doc.status = 'failed'
            doc.index_error = '分块结果为空：未生成可检索片段'
            doc.failed_at = timezone.now()
            doc.save(update_fields=['status', 'index_error', 'failed_at', 'updated_at'])
            return None

        # 3. Embedding
        doc.status = 'embedding'
        doc.save(update_fields=['status', 'updated_at'])

        texts = [c['content'] for c in raw_chunks]
        embeddings = llm_client.embed(texts)
        if not embeddings or len(embeddings) != len(raw_chunks):
            logger.error(f'doc_id={doc_id} Embedding 失败或数量不匹配')
            raise RuntimeError('Embedding 失败')

        # 4. 确保向量集合存在 → 写入 Milvus
        # Remove remnants from an earlier attempt before writing a fresh index.
        # Milvus vector IDs are deterministic, so deleting by document makes retries
        # safe and prevents duplicate chunks from being returned.
        milvus_client.delete_by_doc(doc_id)
        DocChunk.objects.filter(doc_id=doc_id).delete()

        milvus_client.ensure_collection()
        milvus_chunks = []
        for i, c in enumerate(raw_chunks):
            milvus_chunks.append({
                'vector_id': f'doc_{doc_id}_chunk_{i}',
                'doc_id': doc_id,
                'chunk_index': c['chunk_index'],
                'chunk_text': c['content'],
                'page_number': 0,
                'seq_order': i,
            })

        vector_ids = milvus_client.insert_chunks(milvus_chunks, embeddings)

        # 5. 写入 MySQL DocChunk
        doc_chunks = []
        for i, c in enumerate(raw_chunks):
            doc_chunks.append(DocChunk(
                doc=doc,
                chunk_index=c['chunk_index'],
                content=_cleanse_for_mysql_utf8mb3(c['content']),  # 4字节字符清洗
                token_count=0,
                vector_id=vector_ids[i] if i < len(vector_ids) else f'doc_{doc_id}_chunk_{i}',
                page_start=None,
                page_end=None,
            ))

        DocChunk.objects.bulk_create(doc_chunks, batch_size=100)

        # 6. 更新状态
        doc.status = 'indexed'
        doc.index_error = ''
        doc.failed_at = None
        doc.chunk_count = len(raw_chunks)
        doc.indexed_at = timezone.now()
        doc.save(update_fields=[
            'status', 'index_error', 'failed_at', 'chunk_count',
            'indexed_at', 'updated_at',
        ])

        logger.info(
            '索引完成: doc_id=%d  %d 块 → Milvus + MySQL',
            doc_id, len(raw_chunks),
        )
        return None

    except Exception as e:
        logger.error(f'index_document_task doc_id={doc_id} 失败: {e}')
        try:
            final_failure = retries >= max_retries
            if final_failure:
                doc.status = 'failed'
                doc.index_error = f'索引失败：{type(e).__name__}: {str(e)[:500]}'
                doc.failed_at = timezone.now()
                doc.save(update_fields=[
                    'status', 'index_error', 'failed_at', 'updated_at',
                ])
            else:
                # 保留当前阶段，避免重试尚未开始时被误报为最终失败。
                doc.index_error = f'第 {retries + 1} 次尝试失败，将自动重试：{str(e)[:500]}'
                doc.save(update_fields=['index_error', 'updated_at'])
        except Exception:
            pass
        if retries < max_retries and self is not None:
            raise self.retry(exc=e)
        raise


@shared_task(bind=True, ignore_result=True)
def cleanup_expired_upload_sessions_task(self, expire_hours: int = 24):
    """清理超时未完成的分片上传会话。

    对 status=uploading 且 updated_at 超过 expire_hours 的会话：
    1. 调 OSS AbortMultipartUpload 释放云端已传分片占用的存储
    2. 会话标记为 aborted

    异常处理:
        - 单个会话 abort 失败不影响其他会话，等待下轮重试
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.knowledge.chunked_views import SESSION_EXPIRE_HOURS
    from apps.knowledge.models import ChunkedUploadSession
    from utils.oss import oss_client

    expire = timedelta(hours=expire_hours or SESSION_EXPIRE_HOURS)
    expired = ChunkedUploadSession.objects.filter(
        status='uploading', updated_at__lt=timezone.now() - expire,
    )
    count = 0
    for session in expired.iterator():
        try:
            if oss_client.bucket:
                oss_client.bucket.abort_multipart_upload(session.oss_key, session.upload_id)
            session.status = 'aborted'
            session.save(update_fields=['status', 'updated_at'])
            count += 1
        except Exception as e:
            logger.warning(
                f'清理分片会话失败 upload_id={session.upload_id}: {e}'
            )
    logger.info(f'cleanup_expired_upload_sessions_task: 清理过期分片会话 {count} 个')
    return None
