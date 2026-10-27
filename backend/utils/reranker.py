"""
Cross-Encoder 重排序模块

对初检 Top-N 结果进行精排，提升 Top-5 相关性。
支持本地模型（sentence-transformers），模型不可用时自动降级为加权融合。

CPU 环境注意：bge-reranker-v2-m3 在纯 CPU 上单次精排需 40~60s，
曾因叠加二次检索把问答首字拖到 80~100s（超过前端 30s 超时）。
为此内置三道保险，任何情况下精排都不能拖死问答：
1. 硬耗时预算：超时自动放弃精排，回退加权融合排序；
2. 自适应触发：候选过少或融合分 top1 明显领先时跳过；
3. 结果缓存：相同问题+候选集直接命中，零模型调用。
"""
import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import List, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# 默认模型（多语言，支持中文，轻量）
CROSS_ENCODER_MODEL = getattr(
    settings, 'CROSS_ENCODER_MODEL', 'BAAI/bge-reranker-v2-m3',
)
# CPU 环境输入降载：候选 20→8、长度 512→256（实测耗时约为原来的 1/4）
CROSS_ENCODER_TOP_N_INPUT = 8       # 初检取前 8 送入 Cross-Encoder
CROSS_ENCODER_TOP_K_OUTPUT = 5      # Cross-Encoder 输出前 5
CROSS_ENCODER_MAX_LENGTH = 256      # 每段文本最大长度
CROSS_ENCODER_MIN_TRIGGER = 3       # 候选数 >=3 才触发 Rerank（3 以下不值得）
CROSS_ENCODER_TIMEOUT = 5           # 精排硬预算（秒），超时回退融合排序
CROSS_ENCODER_SKIP_GAP = 0.15       # 融合分 top1 领先 top2 超过该值时跳过精排
CROSS_ENCODER_CACHE_TTL = 600       # 精排结果缓存 10 分钟

_cross_encoder = None
_cross_encoder_init_attempted = False

# 单 worker 线程池 + 忙标记：同一时刻最多一个精排在跑；
# 上一次未结束时新请求直接跳过精排，避免 CPU 任务堆积拖垮整站
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rerank')
_busy_lock = threading.Lock()
_busy = False


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


def _cache_key(question: str, input_candidates: List[dict]) -> str:
    """缓存键：问题 + 送入精排的候选 id 序列（内容不参与，避免 key 过长）。"""
    ids = [
        [c.get('doc_id'), c.get('chunk_index')]
        for c in input_candidates
    ]
    raw = json.dumps({'q': question, 'ids': ids}, ensure_ascii=False)
    return 'rerank:' + hashlib.md5(raw.encode('utf-8')).hexdigest()


def _apply_cached(candidates: List[dict], cached: list) -> Optional[List[dict]]:
    """按缓存的精排顺序与分数重排当前候选。

    任一缓存项在当前候选集中找不到（候选集已变化）则返回 None 视为缓存失效。
    """
    by_key = {}
    for c in candidates:
        by_key.setdefault((c.get('doc_id'), c.get('chunk_index')), c)

    ranked, used = [], set()
    for doc_id, chunk_index, score in cached:
        key = (doc_id, chunk_index)
        c = by_key.get(key)
        if c is None or key in used:
            return None
        c['rerank_score'] = float(score)
        c['score'] = c['rerank_score']
        ranked.append(c)
        used.add(key)

    # 缓存只覆盖送入精排的前 N 个候选；其余候选保持原顺序追加
    for c in candidates:
        key = (c.get('doc_id'), c.get('chunk_index'))
        if key not in used:
            ranked.append(c)
    return ranked


def _clear_busy(_future):
    global _busy
    with _busy_lock:
        _busy = False


def rerank_with_cross_encoder(
    question: str,
    candidates: List[dict],
    top_k: int = CROSS_ENCODER_TOP_K_OUTPUT,
) -> List[dict]:
    """
    使用 Cross-Encoder 对候选结果重排序。

    Args:
        question: 用户问题
        candidates: 初检结果列表（按融合分降序）
                    [{'doc_id': int, 'content': str, 'score': float, ...}]
        top_k: 返回数量

    Returns:
        重排序后的结果列表（score 字段替换为 Cross-Encoder 得分）；
        任何异常/超时/忙状态都回退为原候选顺序，保证功能兜底。
    """
    model = _get_cross_encoder()
    if model is None:
        return candidates[:top_k]

    if not candidates:
        return []

    # 候选过少不触发：少于 3 个 Rerank 意义不大
    if len(candidates) < CROSS_ENCODER_MIN_TRIGGER:
        return candidates[:top_k]

    # 自适应跳过：融合排序 top1 已明显领先时，精排不改变结论
    if len(candidates) >= 2:
        s1 = candidates[0].get('score', 0) or 0
        s2 = candidates[1].get('score', 0) or 0
        if s1 - s2 >= CROSS_ENCODER_SKIP_GAP:
            logger.debug('融合分领先明显(top1=%.3f top2=%.3f)，跳过精排', s1, s2)
            return candidates[:top_k]

    # 截取 Top-N 送入 Cross-Encoder
    input_candidates = candidates[:CROSS_ENCODER_TOP_N_INPUT]

    # 缓存命中直接返回，零模型调用（控制 token/CPU 成本）
    cache_key = _cache_key(question, input_candidates)
    try:
        cached = cache.get(cache_key)
    except Exception as e:
        logger.debug('精排缓存读取失败，按未命中处理: %s', e)
        cached = None
    if cached:
        ranked = _apply_cached(candidates, cached)
        if ranked is not None:
            logger.debug('精排缓存命中: %s', cache_key)
            return ranked[:top_k]

    # 构造 (question, content) 对
    pairs = [
        (question, (c.get('content') or '')[:CROSS_ENCODER_MAX_LENGTH])
        for c in input_candidates
    ]

    # 硬预算：线程池执行 + 超时回退；上一次未结束则直接跳过
    global _busy
    with _busy_lock:
        if _busy:
            logger.info('上一次精排未结束，本次跳过（回退融合排序）')
            return candidates[:top_k]
        _busy = True

    try:
        future = _executor.submit(model.predict, pairs, show_progress_bar=False)
        future.add_done_callback(_clear_busy)
    except Exception as e:
        logger.warning('精排任务提交失败: %s', e)
        _clear_busy(None)
        return candidates[:top_k]

    try:
        scores = future.result(timeout=CROSS_ENCODER_TIMEOUT)
    except FuturesTimeout:
        # 超时：后台 predict 继续跑完（无法中断 CPU 计算），由 done 回调释放忙标记；
        # 期间其他请求走忙跳过逻辑，不会堆积
        logger.warning(
            '精排超过 %ds 预算，回退加权融合排序（候选=%d）',
            CROSS_ENCODER_TIMEOUT, len(input_candidates),
        )
        return candidates[:top_k]
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

    try:
        cache.set(cache_key, [
            [c.get('doc_id'), c.get('chunk_index'), c.get('rerank_score', 0)]
            for c in ranked
        ], CROSS_ENCODER_CACHE_TTL)
    except Exception as e:
        logger.debug('精排缓存写入失败，不影响本次结果: %s', e)

    logger.debug(
        'Cross-Encoder 重排序完成: %d → %d', len(input_candidates), len(ranked[:top_k]),
    )
    return ranked[:top_k]
