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
    from apps.knowledge.moderation import check_indexable
    from apps.knowledge.utils import chunk_document
    from utils.llm import llm_client
    from utils.milvus import milvus_client

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'index_document_task: doc_id={doc_id} 不存在')
        return None

    # 索引前置安全校验（集中式唯一入口，见 apps.knowledge.moderation）。
    # 必须放在任务体内：本任务还可能被 safe_delay 同步降级、脚本 task.run()、
    # 爬虫 safe_delay、broker 残留消息等路径直接调用，只加在视图层会被绕过。
    allowed, reason = check_indexable(doc)
    if not allowed:
        logger.warning(f'index_document_task doc_id={doc_id} 未通过索引前置校验: {reason}')
        doc.status = 'failed'
        doc.index_error = f'索引前置校验未通过：{reason}'
        doc.failed_at = timezone.now()
        doc.save(update_fields=['status', 'index_error', 'failed_at', 'updated_at'])
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


def _bound_task_id(bound_task) -> str:
    """取当前 Celery 任务 ID。

    降级同步执行（未安装 Celery / broker 不可用）时 `self` 是 None，没有
    `request`——此时返回 'sync'，而不是让 AttributeError 把整个任务体吞掉
    （那会让审核链路在本地环境变成静默空操作）。与 `index_document_task`
    里 `getattr(self, 'request', None)` 的写法保持一致。
    """
    request = getattr(bound_task, 'request', None)
    return getattr(request, 'id', None) or 'sync'


@shared_task(bind=True, ignore_result=True)
def moderate_and_promote_task(self, doc_id: int):
    """P1-2D STEP 4-B：上传完成后的统一编排 —— 审核 → 提升 → 索引。

    职责边界（刻意保持分离，不要把 promotion 塞回 `moderate_document()`）：

        moderate_document(doc_id)   只写审核结论，绝不改存储位置、绝不索引
        promote_document(doc_id)    只在校验审核结论后做 quarantine → knowledge
        index_document_task(doc_id) 由 promotion 的 on_commit 派发

    本任务自身不做任何安全判定——"能不能提升"由 `promote_document()` 内部的
    白名单判定决定，"能不能索引"由 `check_indexable()` 兜底。这里只是把三段
    串起来，因此**不存在**"编排层漏了一个 if 就放行"的风险面。

    幂等性：
    - 已 `approved + promoted` 时 `promote_document()` 直接短路，不重复 copy；
      此时本任务走"补派索引"路径（promotion 成功但派发丢失的恢复路径），
      且只有 `doc.status != 'indexed'` 时才补派——重复调用不会重复索引。
    - 本次刚 promoted 时不重复派发索引：promotion 的 on_commit 已经派过。
    - 审核结论为 rejected / unavailable / error / pending / scanning 时，
      `promote_document()` 一律 skip，不会产生任何 OSS 或存储状态变更。

    Args:
        doc_id: KnowledgeDoc 主键
    """
    from .models import ContentModeration, KnowledgeDoc
    from .moderation import check_indexable, moderate_document
    from .promotion import PromotionResult, promote_document

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning('moderate_and_promote_task: doc_id=%s 不存在', doc_id)
        return None

    # ---- 1. 审核（只写 ContentModeration / ModerationAttempt） ----
    run = moderate_document(doc_id, worker_id=f'worker-{_bound_task_id(self)}')
    logger.info(
        'moderate_and_promote_task 审核阶段 doc_id=%s state=%s verdict=%s applied=%s',
        doc_id, run.state, run.verdict, run.applied,
    )

    moderation = ContentModeration.objects.filter(doc_id=doc_id).first()
    if moderation is None:
        # 审核服务在写记录之前就失败了（doc 不存在 / 无 oss_key）——
        # 没有任何可提升的事实，直接结束。绝不视为通过。
        logger.warning('moderate_and_promote_task: doc_id=%s 无审核记录，中止', doc_id)
        return None

    # ---- 2. 提升（内部做白名单判定 + CAS + OSS 校验） ----
    promotion = promote_document(doc_id, worker_id=f'task-{_bound_task_id(self)}')

    # ---- 3. 索引派发 ----
    # 两条互斥路径，刻意不重叠，避免同一次调用把同一份文档派发两次
    # （索引会真的调用 Embedding API，重复派发是实打实的成本）：
    #
    #   STATUS_PROMOTED —— 本次刚完成提升：`promote_document()` 已经在事务
    #                      提交后注册了索引派发，这里**不再**重复派发。
    #   STATUS_ALREADY  —— 早已 promoted（幂等短路，没有产生任何 on_commit）：
    #                      这是"提升成功但派发丢失"的恢复路径，在这里补齐。
    #
    # 两条路径都先用 `check_indexable()` 兜底：即使 storage_state 是 promoted，
    # 审核结论为 rejected 时依然不得索引。
    if promotion.status == PromotionResult.STATUS_ALREADY:
        doc.refresh_from_db(fields=['oss_key', 'status'])
        allowed, reason = check_indexable(doc)
        if allowed and doc.status != 'indexed':
            from utils.celery_dispatcher import safe_delay

            safe_delay(index_document_task, doc.id)
            logger.info('moderate_and_promote_task 补派索引 doc_id=%s', doc_id)
        elif not allowed:
            logger.warning(
                'moderate_and_promote_task: doc_id=%s 已提升但仍不可索引: %s',
                doc_id, reason,
            )
    return None


def _recover_undispatched_uploads(limit: int, now, dispatch) -> int:
    """补扫"已上传但从未进入审核队列"的文档（审计报告 P-4 的恢复路径）。

    `upload_service.schedule_moderation()` 走 `dispatch_async`——**只入队，
    绝不降级同步执行**。队列不可用时它只记 ERROR 日志就返回，于是留下这样一种
    文档：KnowledgeDoc 已建、对象仍在 `quarantine/`、**没有任何
    ContentModeration 记录**。这是刻意的 fail-closed（宁可不审，也不放行），
    但必须有恢复路径，否则队列抖一次，那批上传就永久卡在未审核。

    选取条件（全部必须满足）：
        oss_key 以 `quarantine/` 开头   —— 只处理隔离区对象，正式区历史文档不碰
        status == 'uploaded'            —— 从未开始索引
        created_at 早于宽限期            —— 避免与刚提交、正在入队的请求抢同一份
        无 ContentModeration 记录        —— 从未被审核过

    **本函数不做任何放行判定**：它只把文档重新排进 `moderate_and_promote_task`，
    能不能过审仍由 `moderate_document()` 决定、能不能提升仍由
    `promote_document()` 决定。任何"补扫即放行"的改动都属于安全事故。

    Returns:
        本轮重新派发的文档数
    """
    from datetime import timedelta

    from django.conf import settings

    from utils.oss import QUARANTINE_KEY_PREFIX

    from .models import ContentModeration, KnowledgeDoc

    grace = int(getattr(
        settings, 'MODERATION_DISPATCH_RECOVERY_GRACE_SECONDS', 900,
    ))
    cutoff = now - timedelta(seconds=grace)

    candidates = list(
        KnowledgeDoc.objects.filter(
            oss_key__startswith=QUARANTINE_KEY_PREFIX,
            status='uploaded',
            created_at__lt=cutoff,
        ).exclude(
            id__in=ContentModeration.objects.values('doc_id'),
        ).order_by('created_at').values_list('id', flat=True)[:limit]
    )
    if not candidates:
        return 0

    for doc_id in candidates:
        dispatch(moderate_and_promote_task, doc_id)

    logger.warning(
        '补扫到 %d 份"已上传但从未入队审核"的文档，已重新派发'
        '（对象仍在 quarantine/，未做任何放行）',
        len(candidates),
    )
    return len(candidates)


@shared_task(bind=True, ignore_result=True)
def retry_failed_moderations_task(self, limit: int = 50):
    """重试审核未完成的文档（`unavailable` / `error`）的最小 reaper。

    `moderate_document()` 在检测链没能给出结论时会写 `next_retry_at`，
    但不会自己重跑——重试需要一个调度者。本任务就是那个调度者。

    选取条件（全部必须满足）：
        state ∈ {unavailable, error}          —— 绝不重跑 approved / rejected 终态
        next_retry_at 已到期                   —— 使用既有字段，不新增
        attempt_count < CONTENT_MODERATION_MAX_ATTEMPTS —— 不超过上限

    并发与幂等：
    - 派发前用 CAS 把 `next_retry_at` 推到下一个退避点，重复/并发运行的 reaper
      不会对同一文档重复派发。
    - 即使派发出去的任务丢失，退避到期后仍会再次进入，不会静默卡死。
    - **达到 max_attempts 的文档保持 unavailable / error，绝不自动 approved**：
      本任务只重新派发 `moderate_document()`，没有任何"放行"分支。

    Args:
        limit: 单轮最多处理的文档数（避免一次扫描把队列打满）
    """
    from datetime import timedelta

    from django.utils import timezone

    from .models import ContentModeration
    from .moderation import (
        MODERATION_STATE_ERROR,
        MODERATION_STATE_UNAVAILABLE,
        max_attempts,
        retry_backoff_seconds,
    )

    now = timezone.now()
    due = list(
        ContentModeration.objects.filter(
            state__in=(MODERATION_STATE_UNAVAILABLE, MODERATION_STATE_ERROR),
            next_retry_at__isnull=False,
            next_retry_at__lte=now,
            attempt_count__lt=max_attempts(),
        ).order_by('next_retry_at').values_list('pk', 'doc_id')[:limit]
    )

    backoff = timedelta(seconds=retry_backoff_seconds())
    from utils.celery_dispatcher import safe_delay

    dispatched = 0
    for pk, doc_id in due:
        claimed = ContentModeration.objects.filter(
            pk=pk, next_retry_at__lte=now,
        ).update(next_retry_at=now + backoff)
        if not claimed:
            continue  # 被并发运行的另一个 reaper 抢先
        safe_delay(moderate_and_promote_task, doc_id)
        dispatched += 1

    # P-4 恢复路径：队列不可用期间 `dispatch_async` 只记日志不执行，
    # 会留下"有文档、无审核记录"的孤儿。这里把它们重新排队。
    recovered = _recover_undispatched_uploads(limit, now, safe_delay)

    if dispatched or recovered:
        logger.info(
            'retry_failed_moderations_task: 重新派发审核 %d 份，'
            '补扫未入队上传 %d 份', dispatched, recovered,
        )
    return None


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
