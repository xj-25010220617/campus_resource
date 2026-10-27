"""Cross-Encoder 精排保护机制测试（不依赖数据库与真实模型）。"""
import time
from unittest import mock

from django.test import SimpleTestCase

from utils import reranker


class _FakeModel:
    """predict 返回固定分数，可附带耗时模拟 CPU 慢推理。"""

    def __init__(self, scores, sleep=0.0, explode=False):
        self._scores = scores
        self._sleep = sleep
        self._explode = explode
        self.calls = 0

    def predict(self, pairs, show_progress_bar=False):
        self.calls += 1
        if self._explode:
            raise AssertionError('不应触发模型推理')
        if self._sleep:
            time.sleep(self._sleep)
        return self._scores[:len(pairs)]


class _DictCache:
    """替代 Django 缓存的内存实现，隔离文件缓存。"""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value


def _candidates(n, scores=None, gap=False):
    scores = scores or [0.50 - i * 0.01 for i in range(n)]
    if gap:
        scores = [0.90] + [0.50] * (n - 1)
    return [
        {'doc_id': 100 + i, 'chunk_index': i, 'content': f'内容{i}', 'score': scores[i]}
        for i in range(n)
    ]


class RerankerGuardTests(SimpleTestCase):

    def setUp(self):
        # 每个用例重置全局状态，避免相互污染
        self._patches = [
            mock.patch.object(reranker, '_busy', False),
            mock.patch.object(reranker, 'cache', _DictCache()),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _run(self, model, question='测试问题', **kwargs):
        with mock.patch.object(reranker, '_get_cross_encoder', return_value=model):
            return reranker.rerank_with_cross_encoder(question, **kwargs)

    def test_disabled_or_no_model_returns_input(self):
        cands = _candidates(4)
        out = self._run(None, candidates=cands)
        self.assertEqual(out, cands[:5])

    def test_few_candidates_skip(self):
        model = _FakeModel([0.1, 0.2], explode=True)
        cands = _candidates(2)
        out = self._run(model, candidates=cands)
        self.assertEqual(out, cands)

    def test_adaptive_skip_when_top1_clearly_leads(self):
        model = _FakeModel([0.1] * 4, explode=True)
        cands = _candidates(4, gap=True)  # 0.90 vs 0.50，差距 > SKIP_GAP
        out = self._run(model, candidates=cands)
        self.assertEqual(out, cands)

    def test_rerank_applies_scores_and_order(self):
        # 模型把顺序完全颠倒：最后一个候选得分最高
        model = _FakeModel([0.1, 0.2, 0.3, 0.9])
        cands = _candidates(4)
        out = self._run(model, candidates=cands)
        self.assertEqual(model.calls, 1)
        self.assertEqual(out[0]['doc_id'], 103)
        self.assertAlmostEqual(out[0]['score'], 0.9)

    def test_cache_hit_avoids_second_inference(self):
        model = _FakeModel([0.1, 0.2, 0.9, 0.3])
        cands1 = _candidates(4)
        out1 = self._run(model, candidates=cands1)
        # 第二次：候选集相同但为全新对象（模拟新一轮检索）
        cands2 = _candidates(4)
        out2 = self._run(model, candidates=cands2)
        self.assertEqual(model.calls, 1)  # 第二次命中缓存，未再推理
        self.assertEqual(
            [c['doc_id'] for c in out1], [c['doc_id'] for c in out2],
        )
        self.assertEqual(out2[0]['doc_id'], 102)

    def test_timeout_falls_back_and_busy_released(self):
        model = _FakeModel([0.9, 0.1, 0.2, 0.3], sleep=0.4)
        cands = _candidates(4)
        with mock.patch.object(reranker, 'CROSS_ENCODER_TIMEOUT', 0.1):
            out = self._run(model, candidates=cands)
        # 超时回退：保持原融合顺序，分数未被改写
        self.assertEqual([c['doc_id'] for c in out], [100, 101, 102, 103])
        # 后台 predict 跑完后忙标记释放，后续请求可正常精排
        time.sleep(0.5)
        self.assertFalse(reranker._busy)
        out2 = self._run(model, question='另一个问题', candidates=_candidates(4))
        self.assertEqual(out2[0]['doc_id'], 100)  # 0.9 分候选排第一
        self.assertAlmostEqual(out2[0]['score'], 0.9)

    def test_busy_state_skips_rerank(self):
        model = _FakeModel([0.9, 0.1, 0.2, 0.3], explode=True)
        cands = _candidates(4)
        with mock.patch.object(reranker, '_busy', True):
            out = self._run(model, candidates=cands)
        self.assertEqual(out, cands)

    def test_predict_exception_falls_back(self):
        class _BadModel:
            def predict(self, pairs, show_progress_bar=False):
                raise RuntimeError('模型异常')

        with mock.patch.object(reranker, '_get_cross_encoder', return_value=_BadModel()):
            out = reranker.rerank_with_cross_encoder('q', _candidates(4))
        self.assertEqual([c['doc_id'] for c in out], [100, 101, 102, 103])
