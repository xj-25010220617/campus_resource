"""检索级语义缓存（阶段 5.2）单元测试。"""
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.core.cache import cache

from utils import semantic_cache

_LOCMEM = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
}
_HITS = [{'doc_id': 1, 'doc_title': '制度', 'content': '正文', 'score': 0.9}]


@override_settings(CACHES=_LOCMEM, SEMANTIC_CACHE_ENABLED=True, SEMANTIC_CACHE_THRESHOLD=0.95)
class SemanticCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_disabled_returns_none(self):
        with self.settings(SEMANTIC_CACHE_ENABLED=False):
            self.assertIsNone(semantic_cache.lookup('重修怎么申请', {'role': 'user'}, {}, None))

    def test_stat_question_not_cacheable(self):
        self.assertFalse(semantic_cache.is_cacheable('各学院有多少人'))
        self.assertFalse(semantic_cache.is_cacheable('2026 年优秀毕业生名单'))
        self.assertTrue(semantic_cache.is_cacheable('重修怎么申请'))

    def test_store_then_hit(self):
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            semantic_cache.store('重修怎么申请', {'role': 'user'}, {}, None, _HITS)
            got = semantic_cache.lookup('重修怎么申请', {'role': 'user'}, {}, None)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['doc_id'], 1)

    def test_scope_isolation_by_role(self):
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            semantic_cache.store('重修怎么申请', {'role': 'user'}, {}, None, _HITS)
            self.assertIsNone(semantic_cache.lookup('重修怎么申请', {'role': 'admin'}, {}, None))

    def test_below_threshold_misses(self):
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            semantic_cache.store('重修怎么申请', {'role': 'user'}, {}, None, _HITS)
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[0.0, 1.0, 0.0]):
            self.assertIsNone(semantic_cache.lookup('重修流程是什么', {'role': 'user'}, {}, None))

    def test_version_change_invalidates(self):
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            semantic_cache.store('重修怎么申请', {'role': 'user'}, {}, None, _HITS)
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=2), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            self.assertIsNone(semantic_cache.lookup('重修怎么申请', {'role': 'user'}, {}, None))

    def test_insufficient_evidence_not_stored(self):
        flagged = [dict(_HITS[0], evidence_insufficient=True)]
        with mock.patch.object(semantic_cache, '_knowledge_version', return_value=1), \
             mock.patch('utils.llm.llm_client.embed_one', return_value=[1.0, 0.0, 0.0]):
            semantic_cache.store('重修怎么申请', {'role': 'user'}, {}, None, flagged)
            self.assertIsNone(semantic_cache.lookup('重修怎么申请', {'role': 'user'}, {}, None))
