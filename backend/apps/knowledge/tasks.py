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
import time

from django.conf import settings
from typing import Optional

from utils.shared_task_compat import shared_task

logger = logging.getLogger(__name__)


def _embedding_rate_limit(batch_size: int):
    """Embedding 全局限流（Redis 滑动窗口）。

    settings.EMBED_RATE_LIMIT 默认 '80/m'，每分钟最多 80 次 Embedding 调用。
    Redis 不可用时静默跳过——开发/测试环境没有 Redis 也不会炸。

    Args:
        batch_size: 本次 embed 批次大小，仅用于日志（不参与阈值计算，阈值按调用次数计）
    """
    import datetime as _dt
    try:
        import redis as _redis
    except ImportError:
        return
    try:
        # 从 CELERY_BROKER_URL 取 Redis 连接，或 settings.CACHES 的 default
        from django.conf import settings as _s
        broker = getattr(_s, 'CELERY_BROKER_URL', '') or ''
        if not broker.startswith('redis'):
            return
        # 解析 rate limit 字符串：'80/m' → 80
        rate_str = getattr(_s, 'EMBED_RATE_LIMIT', '80/m')
        try:
            limit = int(rate_str.split('/')[0])
        except (ValueError, IndexError):
            limit = 80

        r = _redis.Redis.from_url(broker, decode_responses=True)
        now = _dt.datetime.now()
        key = f'embedding:rate:{now.strftime("%Y%m%d%H%M")}'
        count = r.incr(key)
        if count == 1:
            r.expire(key, 70)  # 留 10s 缓冲避免边界抖动
        if count > limit:
            # 等下一分钟窗口
            seconds_left = 60 - now.second + 1
            if seconds_left <= 0:
                seconds_left = 60
            logger.warning(
                'Embedding rate limit hit (%d > %d). 等待 %ds...',
                count, limit, seconds_left,
            )
            time.sleep(min(seconds_left, 30))
    except Exception as e:
        # Redis 连接失败等静默跳过，不阻塞 Embedding 主链路
        logger.debug('Embedding rate limit skip: %s', type(e).__name__)
        return


@shared_task(bind=True, max_retries=2, default_retry_delay=30, ignore_result=True, queue='parse')
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


@shared_task(bind=True, max_retries=1, default_retry_delay=60, ignore_result=True, queue='sys')
def auto_link_relations_task(self, doc_id: int):
    """上传完成后异步执行 AI 自动关联：分析文档并自动创建与相关文档的有向关联边（文件网）。

    关联目标由 AI 在「上传者可浏览范围内的公开文档」中直接挑选并落边，
    用户无需（也不应）手动选择目标文档，避免暴露他人文档供挑选。

    Args:
        doc_id: KnowledgeDoc 主键

    异常处理:
        - 任意异常 → 自动重试 1 次（间隔 60s）
        - 最终失败仅记录日志，不影响上传主流程；关联可后续由管理员删除修正
    """
    from apps.knowledge.models import KnowledgeDoc
    from apps.knowledge.views import _ai_auto_link_relations

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'auto_link_relations_task: doc_id={doc_id} 不存在')
        return None

    try:
        created = _ai_auto_link_relations(doc)
        logger.info(f'AI 自动关联完成 doc_id={doc_id} 新建 {created} 条关联')
        return None
    except Exception as e:
        logger.error(f'auto_link_relations_task doc_id={doc_id} 失败: {e}')
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=1, default_retry_delay=120, ignore_result=True, queue='sys')
def detect_version_task(self, doc_id: int):
    """索引完成后异步识别"疑似新旧版本"，命中只生成待上传者确认的候选。

    识别阶段不修改旧版文档；上传者确认后旧版才会被标记为历史版本并从问答检索中隔离。

    Args:
        doc_id: 新索引完成的 KnowledgeDoc 主键

    异常处理:
        - 任意异常 → 自动重试 1 次（间隔 120s）
        - 最终失败仅记录日志，不影响上传与索引主流程
    """
    from apps.knowledge.versioning import detect_versions

    try:
        candidate = detect_versions(doc_id)
        if candidate:
            logger.info(
                '版本识别完成 doc_id=%d 候选旧版=%d candidate_id=%d',
                doc_id, candidate.old_doc_id, candidate.id,
            )
        return None
    except Exception as e:
        logger.error(f'detect_version_task doc_id={doc_id} 失败: {e}')
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=120, ignore_result=True, queue='crawl')
def crawl_neusoft_website_task(
    self, max_pages: int = 100,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
    download_files: Optional[bool] = None,
    section_page_limit: int = 200,
):
    """
    爬取成都东软学院官网并索引到知识库。

    断点续爬：已入库页面跳过且不占配额，因此每次运行都会向更深的列表页推进，
    无需人工干预即可持续扩充覆盖范围。

    Args:
        max_pages: 本次爬取最大新增页数
        uploader_id: 上传者用户 ID
        college_id: 所属学院 ID（默认 None=校级公开）
        discipline_id: 学科方向 ID（可选，默认自动推断）
        download_files: 是否下载文档类附件（None=取设置 CRAWLER_DOWNLOAD_FILES）
        section_page_limit: 单栏目本次最多新增页数
    """
    from utils.crawler import crawl_and_index

    try:
        saved = crawl_and_index(
            max_pages=max_pages,
            uploader_id=uploader_id,
            college_id=college_id,
            discipline_id=discipline_id,
            download_files=download_files,
            section_page_limit=section_page_limit,
            trigger='scheduled',
        )
        logger.info(f'官网爬取任务完成: 保存 {saved} 篇文档')
        return saved
    except Exception as e:
        logger.error(f'官网爬取任务失败: {e}')
        raise self.retry(exc=e)


@shared_task(bind=True, ignore_result=True, queue='sys')
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


@shared_task(bind=True, max_retries=2, default_retry_delay=60, ignore_result=True, queue='parse')
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

    from apps.knowledge.extractor import extract_text_from_oss_detailed
    from apps.knowledge.models import DocChunk, KnowledgeDoc
    from apps.knowledge.utils import chunk_document, chunk_records, contextual_embed_text
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

        parsed = extract_text_from_oss_detailed(doc.oss_key, doc.file_type, doc.file_name)
        text = parsed.text
        # 解析不完整（OCR 失败页 / 超页截断）的原因：即使索引成功也要留痕
        parse_warning = parsed.issues()

        if not text or not text.strip():
            # 解析为空 → 明确失败。不再用标题/描述/文件名/关键词冒充正文：
            # 「没解析出内容」必须表现为失败，而不是把空内容或元数据当正文
            # 向量化后写进 Milvus（那会让失败文档以 indexed 形态混进检索结果）。
            reason = parse_warning or '解析结果为空：未提取到任何正文'
            logger.warning(f'doc_id={doc_id} 解析为空，判定失败：{reason}')
            doc.status = 'failed'
            doc.index_error = f'解析失败：{reason}'[:500]
            doc.failed_at = timezone.now()
            doc.save(update_fields=['status', 'index_error', 'failed_at', 'updated_at'])
            return None

        if parse_warning:
            logger.warning(f'doc_id={doc_id} 解析不完整（{parse_warning}），按已解析部分索引')

        # 2. 分块
        doc.status = 'chunking'
        doc.save(update_fields=['status', 'updated_at'])

        page_texts = getattr(parsed, 'pages', None) or []
        child_size = int(getattr(settings, 'CHUNK_CHILD_SIZE', 350) or 350)
        child_overlap = int(getattr(settings, 'CHUNK_CHILD_OVERLAP', 80) or 80)
        parent_size = int(getattr(settings, 'CHUNK_PARENT_SIZE', 1200) or 1200)

        if len(page_texts) > 1:
            # 多页文档（PDF）：按页分块，块携带页码，引用才能定位到具体页。
            # 跨页不分块——切在页边界上比切在句子中间更安全，代价是每页
            # 各自保留 overlap，块数略增。
            raw_chunks = []
            for page in page_texts:
                page_chunks = (
                    chunk_records(page['text'], chunk_size=500)
                    if parsed.structured
                    else chunk_document(
                        page['text'], chunk_size=child_size,
                        overlap=child_overlap, parent_size=parent_size,
                    )
                )
                offset = len(raw_chunks)
                for chunk in page_chunks:
                    chunk['page_start'] = page['page_no']
                    chunk['page_end'] = page['page_no']
                    # chunk_index/parent_index 都是页内序号，拼到全文时统一加偏移
                    if chunk.get('parent_index', -1) >= 0:
                        chunk['parent_index'] += offset
                    chunk['chunk_index'] = offset + chunk['chunk_index']
                    raw_chunks.append(chunk)
        else:
            raw_chunks = chunk_records(text, chunk_size=500) if parsed.structured \
                else chunk_document(
                    text, chunk_size=child_size,
                    overlap=child_overlap, parent_size=parent_size,
                )
            if len(page_texts) == 1:
                for chunk in raw_chunks:
                    chunk['page_start'] = page_texts[0]['page_no']
                    chunk['page_end'] = page_texts[0]['page_no']
        if not raw_chunks:
            logger.warning(f'doc_id={doc_id} 分块结果为空，跳过索引')
            doc.status = 'failed'
            doc.index_error = '分块结果为空：未生成可检索片段'
            doc.failed_at = timezone.now()
            doc.save(update_fields=['status', 'index_error', 'failed_at', 'updated_at'])
            return None

        # 父块只入 MySQL（供检索命中子块后回填全文），子块才进向量库
        child_chunks = [c for c in raw_chunks if c.get('chunk_level', 1) != 0]

        # 3. Embedding（只对子块）
        doc.status = 'embedding'
        doc.save(update_fields=['status', 'updated_at'])

        texts = [c['content'] for c in child_chunks]
        if getattr(settings, 'CONTEXTUAL_EMBED_ENABLED', False):
            # Contextual Retrieval：向量化文本前置「标题 > 标题路径」，
            # 只改送 Embedding 的文本，落库内容仍是原始片段。
            texts = [contextual_embed_text(doc.title, c) for c in child_chunks]

        # Embedding 全局限流：百炼免费配额每天 200w token，高峰期多任务并发
        # 会同时打爆配额。80/m 滑动窗口（Redis INCR + EXPIRE）让单任务内批次
        # 串行化，Redis 不可用时降级为直接调用。
        _embedding_rate_limit(len(texts))

        embeddings = llm_client.embed(texts)
        if not embeddings or len(embeddings) != len(child_chunks):
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
        for i, c in enumerate(child_chunks):
            milvus_chunks.append({
                'vector_id': f'doc_{doc_id}_chunk_{i}',
                'doc_id': doc_id,
                'chunk_index': c['chunk_index'],
                'chunk_text': c['content'],
                'page_number': c.get('page_start') or 0,
                'seq_order': i,
                'parent_index': int(c.get('parent_index', -1)),
            })

        vector_ids = milvus_client.insert_chunks(milvus_chunks, embeddings)
        vector_map = {c['chunk_index']: vector_ids[i] for i, c in enumerate(child_chunks)
                      if i < len(vector_ids)}

        # 5. 写入 MySQL DocChunk（子块 + 父块）
        doc_chunks = []
        for c in raw_chunks:
            doc_chunks.append(DocChunk(
                doc=doc,
                chunk_index=c['chunk_index'],
                content=c['content'],
                token_count=0,
                vector_id=vector_map.get(c['chunk_index'], ''),
                page_start=c.get('page_start'),
                page_end=c.get('page_end'),
                chunk_level=int(c.get('chunk_level', 1)),
                parent_index=int(c.get('parent_index', -1)),
                heading_path=(c.get('heading_path') or '')[:500],
                char_start=int(c.get('char_start', 0)),
                char_end=int(c.get('char_end', 0)),
            ))

        DocChunk.objects.bulk_create(doc_chunks, batch_size=100)

        # 6. 更新状态
        doc.status = 'indexed'
        # 保留「部分解析」提示：超页截断 / OCR 失败页必须可被后续排查看到，
        # 否则不完整解析会被当成完整成功。
        doc.index_error = parse_warning[:500]
        doc.failed_at = None
        doc.chunk_count = len(child_chunks)
        doc.indexed_at = timezone.now()
        # 表格结构元数据（仅 xlsx/csv 结构化文档非空），供表格问答引擎生成 SQL
        doc.table_schema = parsed.table_schema
        doc.save(update_fields=[
            'status', 'index_error', 'failed_at', 'chunk_count',
            'indexed_at', 'table_schema', 'updated_at',
        ])

        logger.info(
            '索引完成: doc_id=%d  子块 %d / 父块 %d → Milvus + MySQL',
            doc_id, len(child_chunks), len(raw_chunks) - len(child_chunks),
        )
        # 索引成功后再做新旧版本识别：识别依赖已落库的 DocChunk 文本
        try:
            from utils.celery_dispatcher import safe_delay
            safe_delay(detect_version_task, doc_id)
        except Exception as e:
            logger.warning(f'版本识别任务派发失败 doc_id={doc_id}: {e}')
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


@shared_task(bind=True, ignore_result=True, queue='sys')
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
