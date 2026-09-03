"""
FAQ 自动生成 — Celery 定时任务

流程:
  阶段1: 采集近一周用户问题
  阶段2: 向量聚类合并相似问题
  阶段3: 按占比筛选高频簇
  阶段4: LLM 生成 FAQ 草稿

调度: 每周一 03:00（通过 Celery Beat）
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from utils.shared_task_compat import shared_task

from apps.chat.models import ChatMessage
from utils.llm import llm_client

from .models import FAQItem, FAQGenerationLog
from .utils import (
    cluster_similar_questions,
    filter_by_ratio,
    generate_faq_draft,
    generate_faq_from_source,
    cosine_similarity,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def generate_faq_weekly(self, log_id=None):
    """
    主入口：每周 FAQ 自动提取流程。

    由 Celery Beat 定时触发（每周一 03:00）。
    也可手动调用: python manage.py shell -c "from apps.faq.tasks import generate_faq_weekly; generate_faq_weekly()"
    """
    # API 触发时复用已创建的日志；Beat/命令行则自行创建日志。
    if log_id:
        log = FAQGenerationLog.objects.get(id=log_id)
        if log.status != 'running':
            logger.warning('FAQ 生成日志已结束（log_id=%d, status=%s）', log.id, log.status)
            return
    else:
        # 防重叠：检查是否有正在运行的
        running_log = FAQGenerationLog.objects.filter(status='running').first()
        if running_log:
            # 超过 2 小时视为僵死，允许重新运行
            if running_log.started_at and running_log.started_at > timezone.now() - timedelta(hours=2):
                logger.warning('FAQ 生成任务已在运行中（log_id=%d），跳过本次触发', running_log.id)
                return
            logger.warning('发现僵死的 FAQ 生成任务（log_id=%d），重新开始', running_log.id)
            running_log.status = 'failed'
            running_log.error_message = '超时自动标记为失败'
            running_log.completed_at = timezone.now()
            running_log.save(update_fields=['status', 'error_message', 'completed_at'])
        log = FAQGenerationLog.objects.create(status='running')
    logger.info('FAQ 自动生成开始（log_id=%d）', log.id)

    try:
        # ---- 阶段1: 采集 ----
        since = timezone.now() - timedelta(days=getattr(settings, 'FAQ_ANALYSIS_DAYS', 7))
        messages = (
            ChatMessage.objects
            .filter(role='user', created_at__gte=since)
            .select_related('conversation')
            .only('content', 'conversation_id', 'created_at')
        )

        total_questions = messages.count()
        logger.info('阶段1: 采集近7天用户问题 %d 条', total_questions)

        if total_questions == 0:
            log.status = 'completed'
            log.total_questions = 0
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'total_questions', 'completed_at'])
            logger.info('无用户问题日志，FAQ 生成结束')
            return

        # 去重 + 获取对应 AI 回答
        seen = set()
        question_answer_pairs = []
        for msg in messages:
            content = msg.content.strip()
            if not content or content in seen:
                continue
            seen.add(content)

            # 获取对应 AI 回答（紧跟着的同会话下一条 assistant 消息）
            ai_reply = (
                ChatMessage.objects
                .filter(
                    conversation_id=msg.conversation_id,
                    role='assistant',
                    created_at__gt=msg.created_at,
                )
                .order_by('created_at')
                .first()
            )
            answer = ai_reply.content if ai_reply else ''

            question_answer_pairs.append((content, answer))

        log.total_questions = total_questions
        log.save(update_fields=['total_questions'])
        logger.info('去重后获得 %d 个独立问题', len(question_answer_pairs))

        # ---- 阶段2: 向量聚类 ----
        clusters = cluster_similar_questions(question_answer_pairs)
        log.clusters_found = len(clusters)
        log.save(update_fields=['clusters_found'])

        if not clusters:
            log.status = 'completed'
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'completed_at'])
            logger.info('未形成有效问题簇，FAQ 生成结束')
            return

        # ---- 阶段3: 比例筛选 ----
        high_freq_clusters = filter_by_ratio(clusters, total_questions)
        logger.info('阶段3: 高频簇 %d 个', len(high_freq_clusters))

        if not high_freq_clusters:
            log.status = 'completed'
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'completed_at'])
            logger.info('无高频问题簇，FAQ 生成结束')
            return

        # ---- 阶段4: 生成 FAQ 草稿 ----
        drafts_created = 0
        dedup_threshold = getattr(settings, 'FAQ_DEDUP_THRESHOLD', 0.90)

        try:
            # 一次性取出已有 FAQ 草稿/已发布，用于去重比较
            existing_qs = FAQItem.objects.filter(
                status__in=['draft', 'published'],
            ).exclude(question='').order_by('-created_at')[:50]
            existing_questions = [e.question[:200] for e in existing_qs]
            # 批量嵌入已有 FAQ（一次 HTTP 请求，最坏 100s 兜底）
            existing_vectors = []
            if existing_questions:
                try:
                    existing_vectors = llm_client.embed(existing_questions) or []
                except Exception as e:
                    logger.warning('批量嵌入已有 FAQ 失败，跳过去重：%s', e)
                    existing_vectors = []

            for cluster in high_freq_clusters:
                try:
                    representative = cluster.questions[0][:200]

                    # 批量嵌入：1 个代表问题 + N 个已有 FAQ 一起请求
                    # 但每次只嵌入一个簇的代表问题，再复用 existing_vectors 做比较
                    cluster_vectors = []
                    try:
                        cluster_vectors = llm_client.embed([representative]) or []
                    except Exception as e:
                        logger.warning('簇代表问题嵌入失败，跳过该簇: %s', e)
                        continue
                    if not cluster_vectors:
                        continue
                    cluster_vector = cluster_vectors[0]

                    # 与已有 FAQ 比相似度（复用已批量算好的向量）
                    should_skip = False
                    for exist_vec in existing_vectors:
                        if cosine_similarity(cluster_vector, exist_vec) >= dedup_threshold:
                            should_skip = True
                            logger.info(
                                '跳过已存在的 FAQ: %s（相似度 >= %.2f）',
                                representative[:40], dedup_threshold,
                            )
                            break

                    if should_skip:
                        continue

                    # 调用 LLM 生成
                    result = generate_faq_draft(cluster)
                    if not result:
                        logger.warning('LLM 生成失败，跳过该簇')
                        continue

                    # 保存为草稿
                    FAQItem.objects.create(
                        question=result['question'][:500],
                        answer=result['answer'],
                        tags=result.get('tags', []),
                        college_id=result.get('college_id'),
                        status='draft',
                        frequency=round((cluster.size / total_questions) * 100, 2),
                        variant_count=cluster.size,
                        source_period_start=since,
                        source_period_end=timezone.now(),
                    )
                    drafts_created += 1
                    logger.info('生成 FAQ 草稿: %s', result['question'][:40])

                except Exception as e:
                    logger.error(f'处理问题簇时出错: {e}')
                    continue
        finally:
            # 无论阶段 4 是否成功，强制收尾日志为终态
            # 避免之前那样 "running 永远卡住" 的问题（log_id=1/3 都是这种情况）
            if log.status == 'running':
                log.drafts_generated = drafts_created
                log.completed_at = timezone.now()
                if drafts_created > 0:
                    log.status = 'completed'
                else:
                    log.status = 'completed'  # 即使 0 草稿也视为完成（无崩溃）
                    if not log.error_message:
                        log.error_message = '已完成阶段 1-3，阶段 4 未产出草稿（可能 LLM 调用全部降级到 Mock）'
                log.save(update_fields=[
                    'status', 'drafts_generated', 'completed_at', 'error_message',
                ])
                logger.info(
                    'FAQ 生成最终状态: log_id=%d status=%s drafts=%d',
                    log.id, log.status, drafts_created,
                )
    except Exception as e:
        logger.exception('FAQ 自动生成失败（log_id=%d）', log.id)
        if log.status == 'running':
            log.status = 'failed'
            log.error_message = str(e)[:2000]
            log.completed_at = timezone.now()
            log.save(update_fields=['status', 'error_message', 'completed_at'])
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def generate_faq_from_website(self, log_id=None, max_pages=None, max_docs=None):
    """抓取学校官网，并仅从抓取文档中生成待审核 FAQ 草稿。"""
    from apps.knowledge.extractor import extract_text_from_oss
    from apps.knowledge.models import KnowledgeDoc
    from utils.crawler import crawl_and_index

    max_pages = max_pages or getattr(settings, 'FAQ_WEBSITE_MAX_PAGES', 20)
    max_docs = max_docs or getattr(settings, 'FAQ_WEBSITE_MAX_DOCS', 20)
    max_pages = max(1, min(int(max_pages), 500))
    max_docs = max(1, min(int(max_docs), 100))
    if log_id:
        log = FAQGenerationLog.objects.get(id=log_id)
        if log.status != 'running':
            return
    else:
        active_log = FAQGenerationLog.objects.filter(status='running').first()
        if active_log and active_log.started_at > timezone.now() - timedelta(hours=2):
            logger.warning('FAQ 官网生成任务已在运行中（log_id=%d）', active_log.id)
            return
        log = FAQGenerationLog.objects.create(status='running')

    try:
        saved_count = crawl_and_index(max_pages=max_pages)
        docs = list(
            KnowledgeDoc.objects.filter(
                source_type='website', visibility='public', status__in=['uploaded', 'indexed'],
            ).order_by('-created_at')[:max_docs]
        )
        log.total_questions = len(docs)
        log.clusters_found = saved_count
        log.save(update_fields=['total_questions', 'clusters_found'])

        created = 0
        existing_faqs = FAQItem.objects.filter(status__in=['draft', 'published'])
        existing_questions = set(existing_faqs.values_list('question', flat=True))
        processed_doc_ids = {
            doc_id
            for related_doc_ids in existing_faqs.values_list('related_doc_ids', flat=True)
            for doc_id in (related_doc_ids or [])
        }
        for doc in docs:
            # A document already represented by a draft or published FAQ is
            # immutable for this pipeline; do not generate a second draft.
            if doc.id in processed_doc_ids:
                continue
            text = extract_text_from_oss(doc.oss_key, doc.file_type)
            result = generate_faq_from_source(doc.title, text, doc.source_url)
            if not result or result['question'] in existing_questions:
                continue
            FAQItem.objects.create(
                question=result['question'],
                answer=result['answer'],
                tags=result['tags'],
                status='draft',
                college_id=_resolve_college_id(result['college_name']),
                related_doc_ids=[doc.id],
                source_period_start=doc.created_at,
                source_period_end=timezone.now(),
            )
            existing_questions.add(result['question'])
            processed_doc_ids.add(doc.id)
            created += 1

        log.status = 'completed'
        log.drafts_generated = created
        log.completed_at = timezone.now()
        if not created:
            log.error_message = '官网文档已处理，但未发现可生成的可审核 FAQ。'
        log.save(update_fields=['status', 'drafts_generated', 'completed_at', 'error_message'])
        logger.info('FAQ 官网生成完成（log_id=%d, docs=%d, drafts=%d）', log.id, len(docs), created)
        return log.id
    except Exception as exc:
        logger.exception('FAQ 官网生成失败（log_id=%d）', log.id)
        log.status = 'failed'
        log.error_message = str(exc)[:2000]
        log.completed_at = timezone.now()
        log.save(update_fields=['status', 'error_message', 'completed_at'])
        raise


def _resolve_college_id(college_name):
    if not college_name or college_name == '全局':
        return None
    from apps.accounts.models import College
    college = College.objects.filter(name__icontains=college_name).first()
    return college.id if college else None
