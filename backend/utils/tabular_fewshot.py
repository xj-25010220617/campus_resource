"""Text-to-SQL 少样本示例库（阶段 4.3）。

成功执行的问答对自动入库（verified=False）；只有管理员把 verified 置 True
后才会被检索并注入 Prompt——避免"错误 SQL 教坏模型"。
"""
import hashlib
import json
import logging
from typing import List

from django.conf import settings

logger = logging.getLogger(__name__)


def _normalize(question: str) -> str:
    return ' '.join((question or '').split())[:500]


def _enabled() -> bool:
    return bool(getattr(settings, 'TABULAR_FEWSHOT_ENABLED', False))


def record_example(question: str, sql: str, doc_title: str = '') -> None:
    """成功执行后登记示例；已存在则更新 SQL，不覆盖 verified 状态。"""
    if not _enabled():
        return
    q = _normalize(question)
    if not q or not sql:
        return
    try:
        from apps.knowledge.models import TabularFewShot
        from utils.llm import llm_client

        vec = llm_client.embed_one(q) or []
        TabularFewShot.objects.update_or_create(
            question_hash=hashlib.md5(q.encode('utf-8')).hexdigest(),
            defaults={
                'question': q,
                'sql': sql[:2000],
                'doc_title': (doc_title or '')[:255],
                'embedding': json.dumps(vec) if vec else '',
            },
        )
    except Exception as exc:
        logger.warning('少样本示例登记失败（忽略）：%s', type(exc).__name__)


def retrieve_examples(question: str, top_n: int = None) -> List[dict]:
    """按问题向量余弦检索已验证示例；未开启/失败返回空列表。"""
    if not _enabled():
        return []
    try:
        from apps.knowledge.models import TabularFewShot
        from utils.llm import llm_client

        top_n = top_n or int(getattr(settings, 'TABULAR_FEWSHOT_TOP_N', 3) or 3)
        rows = list(TabularFewShot.objects.filter(verified=True)[:200])
        if not rows:
            return []

        vec = llm_client.embed_one(question)
        if not vec:
            return []

        def cosine(a, b):
            if not a or not b or len(a) != len(b):
                return 0.0
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0

        scored = []
        for row in rows:
            try:
                emb = json.loads(row.embedding or '[]')
            except ValueError:
                continue
            scored.append((cosine(vec, emb), row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {'question': row.question, 'sql': row.sql}
            for score, row in scored[:top_n] if score >= 0.75
        ]
    except Exception as exc:
        logger.warning('少样本检索失败（忽略）：%s', type(exc).__name__)
        return []


def format_examples(question: str) -> str:
    examples = retrieve_examples(question)
    if not examples:
        return ''
    return '\n'.join(
        f"{i}. 问：{e['question']}\n   SQL：{e['sql']}" for i, e in enumerate(examples, 1)
    )
