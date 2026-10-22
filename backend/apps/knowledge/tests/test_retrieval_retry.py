"""二次检索（检索结果为空/低置信时扩大范围重试）的回归测试。

只验证编排逻辑：检索管道本身（Milvus/关键词/精排）在原有测试中覆盖，
这里用 mock 替换 _retrieve_pipeline，避免依赖向量库与数据库。
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from utils import rag


def _hit(doc_id, score, chunk_index=0):
    return {
        'doc_id': doc_id,
        'chunk_index': chunk_index,
        'score': score,
        'content': f'doc-{doc_id}',
        'doc_title': f'文档{doc_id}',
    }


@patch('utils.query_rewriter.rewrite_query', return_value=['测试问题'])
class RetrievalRetryTests(SimpleTestCase):
    """retrieve() 的二次检索触发、合并、去重与上限。"""

    def test_confident_first_pass_does_not_retry(self, _rewrite):
        first = [_hit(1, 0.9), _hit(2, 0.7)]
        with patch.object(rag, '_retrieve_pipeline', return_value=first) as pipeline:
            results = rag.retrieve('测试问题')

        self.assertEqual(results, first)
        self.assertEqual(pipeline.call_count, 1)

    def test_empty_first_pass_triggers_widened_retry(self, _rewrite):
        with patch.object(
            rag, '_retrieve_pipeline', side_effect=[[], [_hit(1, 0.8)]],
        ) as pipeline:
            results = rag.retrieve(
                '测试问题',
                parsed_filters={
                    'time_range': ('2025-01-01', '2025-12-31'),
                    'file_type': 'doc',
                    'category_ids': [1],
                    'college_id': 7,
                },
            )

        self.assertEqual(pipeline.call_count, 2)
        retry_kwargs = pipeline.call_args_list[1].kwargs
        self.assertEqual(retry_kwargs['top_k'], rag.RAG_RETRY_TOP_K)
        self.assertEqual(retry_kwargs['min_score'], rag.RAG_RETRY_MIN_SCORE)
        # 时间/类型/分类被放宽，学院范围保留（权限范围不能放宽）
        self.assertNotIn('time_range', retry_kwargs['parsed_filters'])
        self.assertNotIn('file_type', retry_kwargs['parsed_filters'])
        self.assertNotIn('category_ids', retry_kwargs['parsed_filters'])
        self.assertEqual(retry_kwargs['parsed_filters']['college_id'], 7)
        self.assertEqual(results, [_hit(1, 0.8)])

    def test_low_relevance_first_pass_triggers_retry(self, _rewrite):
        first = [_hit(1, 0.35)]
        second = [_hit(1, 0.75), _hit(2, 0.6)]
        with patch.object(rag, '_retrieve_pipeline', side_effect=[first, second]):
            results = rag.retrieve('测试问题')

        self.assertEqual([r['doc_id'] for r in results], [1, 2])
        self.assertEqual(results[0]['score'], 0.75)

    def test_retry_still_empty_returns_empty_and_stops(self, _rewrite):
        with patch.object(
            rag, '_retrieve_pipeline', side_effect=[[], [], [_hit(9, 0.9)]],
        ) as pipeline:
            results = rag.retrieve('测试问题')

        self.assertEqual(results, [])
        # RAG_RETRY_MAX_ATTEMPTS = 1，最多两次管道调用，不会无限重试
        self.assertEqual(pipeline.call_count, 2)

    def test_merge_dedupes_same_chunk_keeping_higher_score(self, _rewrite):
        first = [_hit(1, 0.4, chunk_index=0), _hit(2, 0.45, chunk_index=0)]
        second = [_hit(1, 0.8, chunk_index=0), _hit(3, 0.5, chunk_index=1)]
        with patch.object(rag, '_retrieve_pipeline', side_effect=[first, second]):
            results = rag.retrieve('测试问题')

        doc_ids = [r['doc_id'] for r in results]
        self.assertEqual(doc_ids.count(1), 1)
        self.assertEqual(results[0]['doc_id'], 1)
        self.assertEqual(results[0]['score'], 0.8)
        self.assertEqual(doc_ids, [1, 3, 2])

    def test_rewrite_query_called_once_across_retry(self, rewrite):
        with patch.object(
            rag, '_retrieve_pipeline', side_effect=[[], [_hit(1, 0.8)]],
        ):
            rag.retrieve('测试问题')

        self.assertEqual(rewrite.call_count, 1)

    def test_result_capped_to_first_pass_top_k(self, _rewrite):
        first = [_hit(i, 0.4, chunk_index=i) for i in range(10)]
        second = [_hit(100 + i, 0.9, chunk_index=i) for i in range(10)]
        with patch.object(rag, '_retrieve_pipeline', side_effect=[first, second]):
            results = rag.retrieve('测试问题', top_k=15)

        self.assertEqual(len(results), 15)
        self.assertEqual(results[0]['score'], 0.9)

    def test_pure_time_question_skips_pipeline(self, _rewrite):
        with patch.object(rag, '_retrieve_pipeline') as pipeline:
            results = rag.retrieve('现在几点')

        self.assertEqual(results, [])
        pipeline.assert_not_called()


class RetrievalRetryHelperTests(SimpleTestCase):
    """触发条件与过滤条件放宽的纯函数测试。"""

    def test_should_retry_when_empty(self):
        self.assertEqual(rag._should_retry_retrieval([])[0], True)

    def test_should_retry_when_top_score_low(self):
        need, reason = rag._should_retry_retrieval([_hit(1, 0.2), _hit(2, 0.1)])
        self.assertTrue(need)
        self.assertIn('low_top_score', reason)

    def test_no_retry_when_enough_confident_results(self):
        need, _ = rag._should_retry_retrieval([_hit(1, 0.8), _hit(2, 0.7)])
        self.assertFalse(need)

    def test_widen_filters_drops_only_retrieval_restrictions(self):
        widened = rag._widen_retrieval_filters({
            'time_range': ('2025-01-01', '2025-12-31'),
            'file_type': 'doc',
            'category_ids': [1, 2],
            'college_id': 3,
            'discipline_id': 4,
            'need_history': True,
        })
        self.assertEqual(
            widened,
            {'college_id': 3, 'discipline_id': 4, 'need_history': True},
        )

    def test_widen_filters_handles_none(self):
        self.assertEqual(rag._widen_retrieval_filters(None), {})

    def test_merge_keeps_doc_level_and_chunk_level_entries(self):
        # 文档级结果（无 chunk_index）与分块级结果粒度不同，不做互相覆盖
        merged = rag._merge_retry_results(
            [_hit(1, 0.5, chunk_index=None)],
            [_hit(1, 0.5, chunk_index=2)],
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(
            sorted(r['chunk_index'] if r['chunk_index'] is not None else -1 for r in merged),
            [-1, 2],
        )

    def test_merge_sorted_desc_by_score(self):
        merged = rag._merge_retry_results(
            [_hit(1, 0.2)], [_hit(2, 0.9), _hit(3, 0.5)],
        )
        self.assertEqual([r['doc_id'] for r in merged], [2, 3, 1])
