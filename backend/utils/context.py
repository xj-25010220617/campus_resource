"""
对话上下文工具：集中管理"当前时间 + 每日数据"

设计原则：
1. 每次调用都现场计算，绝不缓存（请求驱动天然新鲜）
2. 所有 LLM Prompt 注入点都从这里取，保证一致性
3. Celery 每日任务可在此注册钩子
"""
import logging
from datetime import datetime
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

WEEKDAY_CN = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']

_last_known_date: Optional[str] = None


def get_current_time_str(now: Optional[datetime] = None) -> str:
    """
    返回当前时间字符串，供 LLM 系统提示注入。

    格式示例："现在是 2026年09月01日 星期二 14:30"
    """
    global _last_known_date
    t = now or timezone.now()
    date_str = t.strftime('%Y年%m月%d日')
    weekday = WEEKDAY_CN[t.weekday()]
    time_str = t.strftime('%H:%M')

    if _last_known_date is not None and _last_known_date != date_str:
        logger.info('检测到日期切换: %s -> %s', _last_known_date, date_str)
    _last_known_date = date_str

    return f"现在是 {date_str} {weekday} {time_str}"


def get_daily_briefing() -> str:
    """
    每日简报 / 数据新鲜度提示。

    返回知识库中最新文档的日期范围，供 LLM 在用户提问含"今年/最新/最近"
    等时间词时判断数据时效。
    """
    try:
        from apps.knowledge.models import KnowledgeDoc
        from django.db.models import Max, Min
        agg = KnowledgeDoc.objects.aggregate(
            doc_count=Max('id'),
            latest=Max('updated_at'),
            earliest=Min('updated_at'),
        )
        if not agg['latest']:
            return ""
        latest = agg['latest'].strftime('%Y-%m-%d')
        earliest = agg['earliest'].strftime('%Y-%m-%d') if agg['earliest'] else latest
        count = agg['doc_count'] or 0
        return (
            f'知识库最新文档日期：{latest}（最早 {earliest}，共 {count} 条）。'
            f'用户问及"今年/最新/最近"且超出该范围时，请明确告知并以上述最新数据回答，'
            f'不可使用训练数据中推断的旧数值。'
        )
    except Exception as e:
        logger.warning('获取知识库最新日期失败: %s', e)
        return ""


def build_system_context() -> str:
    """
    构造统一的系统上下文片段，所有 LLM 调用共享。
    """
    parts = [f"## 当前时间（系统实时注入，请以此为准）\n{get_current_time_str()}"]
    briefing = get_daily_briefing()
    if briefing:
        parts.append(f"## 每日简报\n{briefing}")
    parts.append(
        '注意：涉及"今天/现在/本周"等时间问题，必须使用上述时间，'
        '严禁使用训练数据中的任何日期。'
    )
    return "\n\n".join(parts)
