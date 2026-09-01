"""
Cross-Encoder 重排序模块

对初检 Top-N 结果进行精排，提升 Top-5 相关性。
支持本地模型（sentence-transformers），模型不可用时自动降级为加权融合。
"""
import logging
from typing import Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# 默认模型（多语言，支持中文，轻量）
CROSS_ENCODER_MODEL = getattr(
    settings, 'CROSS_ENCODER_MODEL', 'BAAI/bge-reranker-v2-m3',
)
CROSS_ENCODER_TOP_N_INPUT = 20      # 初检取前 20 送入 Cross-Encoder
CROSS_ENCODER_TOP_K_OUTPUT = 5      # Cross-Encoder 输出前 5
CROSS_ENCODER_MAX_LENGTH = 512      # 每段文本最大长度
CROSS_ENCODER_MIN_TRIGGER = 3       # 候选数 >=3 才触发 Rerank（3 以下不值得）

_cross_encoder = None
_cross_encoder_init_attempted = False


def _get_cross_encoder():
    """懒加载 Cross-Encoder 模型单例（线程安全靠 GIL）。"""
    global _cross_encoder, _cross_encoder_init_attempted

    if _cross_encoder_init_attempted:
        return _cross_encoder if _cross_encoder is not False else None

    _cross_encoder_init_attempted = True

    if not getattr(settings, 'CROSS_ENCODER_ENABLED', True):
        logger.debug('Cross-Encoder 已通过配置禁用')
        _cross_encoder = False
        return None

    try:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder(
            CROSS_ENCODER_MODEL,
            max_length=CROSS_ENCODER_MAX_LENGTH,
        )
        logger.info('Cross-Encoder 加载成功: %s', CROSS_ENCODER_MODEL)
        return _cross_encoder
    except ImportError:
        logger.warning(
            'sentence-transformers 未安装，Cross-Encoder 重排序不可用。'
            '安装: pip install sentence-transformers'
        )
    except Exception as e:
        logger.warning('Cross-Encoder 加载失败 (%s): %s', CROSS_ENCODER_MODEL, e)

    _cross_encoder = False
    return None


def rerank_with_cross_encoder(
    question: str,
    candidates: List[dict],
    top_k: int = CROSS_ENCODER_TOP_K_OUTPUT,
) -> List[dict]:
    """
    使用 Cross-Encoder 对候选结果重排序。

    Args:
        question: 用户问题
        candidates: 初检结果列表
                    [{'doc_id': int, 'content': str, 'score': float, ...}]
        top_k: 返回数量

    Returns:
        重排序后的结果列表（score 字段替换为 Cross-Encoder 得分）
    """
    model = _get_cross_encoder()
    if model is None:
        return candidates[:top_k]

    if not candidates:
        return []

    # 候选过少不触发：少于 3 个 Rerank 意义不大
    if len(candidates) < CROSS_ENCODER_MIN_TRIGGER:
        return candidates[:top_k]

    # 截取 Top-N 送入 Cross-Encoder
    input_candidates = candidates[:CROSS_ENCODER_TOP_N_INPUT]

    # 构造 (question, content) 对
    pairs = [
        (question, (c.get('content') or '')[:CROSS_ENCODER_MAX_LENGTH])
        for c in input_candidates
    ]

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning('Cross-Encoder 预测失败: %s', e)
        return candidates[:top_k]

    # 将 Cross-Encoder 得分写入候选集
    for i, c in enumerate(input_candidates):
        c['rerank_score'] = float(scores[i]) if i < len(scores) else c.get('score', 0)
        c['score'] = c['rerank_score']

    # 按 rerank_score 降序排列
    ranked = sorted(
        input_candidates,
        key=lambda x: x.get('rerank_score', 0),
        reverse=True,
    )

    logger.debug(
        'Cross-Encoder 重排序完成: %d → %d', len(input_candidates), len(ranked[:top_k]),
    )
    return ranked[:top_k]
