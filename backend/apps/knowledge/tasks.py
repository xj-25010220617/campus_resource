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
    college_id: int = 1,
    discipline_id: int = None,
):
    """
    爬取成都东软学院官网并索引到知识库。

    Args:
        max_pages: 本次爬取最大页数
        uploader_id: 上传者用户 ID
        college_id: 所属学院 ID（爬取的内容归入该学院）
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

    try:
        # 1. 解析：提取文本
        logger.info(f'开始索引 doc_id={doc_id} title="{doc.title}"')
        doc.status = 'parsing'
        doc.save(update_fields=['status', 'updated_at'])

        text = extract_text_from_oss(doc.oss_key, doc.file_type)
        if not text or not text.strip():
            logger.warning(f'doc_id={doc_id} 文本提取为空，跳过索引')
            doc.status = 'failed'
            doc.save(update_fields=['status', 'updated_at'])
            return None

        # 2. 分块
        doc.status = 'chunking'
        doc.save(update_fields=['status', 'updated_at'])

        raw_chunks = chunk_document(text, chunk_size=500, overlap=100)
        if not raw_chunks:
            logger.warning(f'doc_id={doc_id} 分块结果为空，跳过索引')
            doc.status = 'failed'
            doc.save(update_fields=['status', 'updated_at'])
            return None

        # 3. Embedding
        doc.status = 'embedding'
        doc.save(update_fields=['status', 'updated_at'])

        texts = [c['content'] for c in raw_chunks]
        embeddings = llm_client.embed(texts)
        if not embeddings or len(embeddings) != len(raw_chunks):
            logger.error(f'doc_id={doc_id} Embedding 失败或数量不匹配')
            raise RuntimeError('Embedding 失败')

        # 4. 写入 Milvus
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
        doc.chunk_count = len(raw_chunks)
        doc.indexed_at = timezone.now()
        doc.save(update_fields=['status', 'chunk_count', 'indexed_at', 'updated_at'])

        logger.info(
            '索引完成: doc_id=%d  %d 块 → Milvus + MySQL',
            doc_id, len(raw_chunks),
        )
        return None

    except Exception as e:
        logger.error(f'index_document_task doc_id={doc_id} 失败: {e}')
        try:
            doc.status = 'failed'
            doc.save(update_fields=['status', 'updated_at'])
        except Exception:
            pass
        raise self.retry(exc=e)
