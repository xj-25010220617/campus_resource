"""意图路由 LLM 兜底测试。

规则命中不应调用模型；规则未命中时才调用一次，并对无效输出、
超时、异常、非法类型做安全回退。所有模型调用均通过测试替身注入。
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from utils.llm_provider import QwenProvider
from utils.query_router import route_question, route_text_question

LLM_SETTINGS = dict(
    QUERY_ROUTER_LLM_ENABLED=True,
    QUERY_ROUTER_LLM_TIMEOUT=1.0,
    QUERY_ROUTER_LLM_CACHE_TTL=600,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
    },
)


@override_settings(**LLM_SETTINGS)
class QueryRouterLLMFallbackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _route_with(self, question, llm_output):
        with patch(
            'utils.query_router._call_llm_classifier', return_value=llm_output,
        ) as classifier:
            route = route_question(question)
        return route, classifier

    # ---- 规则快路径 ----

    def test_rule_hit_does_not_call_llm(self):
        route, classifier = self._route_with(
            '我上传了多少文件',
            '{"type":"rag"}',
        )
        self.assertEqual(route['type'], 'data_query')
        self.assertEqual(route['query_type'], 'count_docs')
        classifier.assert_not_called()

    def test_howto_question_is_general_without_llm(self):
        route, classifier = self._route_with(
            '怎么下载文件',
            '{"type":"data_query","query_type":"find_docs"}',
        )
        self.assertEqual(route['type'], 'general')
        classifier.assert_not_called()

    def test_allow_llm_false_skips_fallback(self):
        with patch('utils.query_router._call_llm_classifier') as classifier:
            route = route_text_question(
                '我上传了多少课件', None, context=None, allow_llm=False,
            )
        self.assertEqual(route['type'], 'rag')
        classifier.assert_not_called()

    # ---- 规则未命中：模型正常输出 ----

    def test_rule_miss_llm_rag(self):
        route, classifier = self._route_with(
            '马克思主义基本原理的课程目标是什么',
            '{"type":"rag"}',
        )
        self.assertEqual(route['type'], 'rag')
        self.assertEqual(route['source'], 'llm')
        classifier.assert_called_once()

    def test_rule_miss_llm_general(self):
        route, _ = self._route_with('今天心情不错呀', '{"type":"general"}')
        self.assertEqual(route['type'], 'general')
        self.assertEqual(route['source'], 'llm')

    def test_rule_miss_llm_data_query_uses_local_params(self):
        route, _ = self._route_with(
            '我上传了多少课件',
            '{"type":"data_query","query_type":"count_docs",'
            '"params":{"doc_id":999,"sql":"drop table"}}',
        )
        self.assertEqual(route['type'], 'data_query')
        self.assertEqual(route['query_type'], 'count_docs')
        self.assertTrue(route['params']['mine_only'])
        # 模型提供的参数一律丢弃，只保留本地推导出的安全参数
        self.assertNotIn('doc_id', route['params'])
        self.assertNotIn('sql', route['params'])

    def test_model_output_with_surrounding_text_is_parsed(self):
        route, _ = self._route_with(
            '数据库课程主要讲什么',
            '分类结果如下：{"type":"rag"} 以上。',
        )
        self.assertEqual(route['type'], 'rag')

    # ---- 无效输出 / 超时 / 异常：安全回退 ----

    def test_invalid_json_falls_back_to_rag(self):
        route, classifier = self._route_with('数据库课程主要讲什么', 'not-json')
        self.assertEqual(route['type'], 'rag')
        self.assertNotIn('source', route)
        classifier.assert_called_once()

    def test_invalid_type_falls_back_to_rag(self):
        route, _ = self._route_with(
            '数据库课程主要讲什么', '{"type":"execute_sql"}',
        )
        self.assertEqual(route['type'], 'rag')

    def test_invalid_data_query_type_falls_back_to_rag(self):
        route, _ = self._route_with(
            '我上传了多少课件',
            '{"type":"data_query","query_type":"drop_table"}',
        )
        self.assertEqual(route['type'], 'rag')

    def test_timeout_falls_back_to_rag(self):
        with patch(
            'utils.query_router._call_llm_classifier',
            side_effect=TimeoutError('timeout'),
        ) as classifier:
            route = route_question('数据库课程主要讲什么')
        self.assertEqual(route['type'], 'rag')
        classifier.assert_called_once()

    def test_exception_falls_back_to_rag(self):
        with patch(
            'utils.query_router._call_llm_classifier',
            side_effect=RuntimeError('boom'),
        ):
            route = route_question('数据库课程主要讲什么')
        self.assertEqual(route['type'], 'rag')

    def test_empty_model_output_falls_back_to_rag(self):
        route, _ = self._route_with('数据库课程主要讲什么', None)
        self.assertEqual(route['type'], 'rag')

    # ---- 调用次数控制 ----

    def test_classifier_uses_short_timeout_and_single_retry(self):
        with patch(
            'utils.llm.llm_client.chat', return_value='{"type":"rag"}',
        ) as chat:
            route = route_question('数据库课程主要讲什么')
        self.assertEqual(route['type'], 'rag')
        kwargs = chat.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 1.0)
        self.assertEqual(kwargs['max_retries'], 1)
        self.assertEqual(kwargs['max_tokens'], 64)

    def test_same_question_is_cached(self):
        with patch(
            'utils.query_router._call_llm_classifier',
            return_value='{"type":"rag"}',
        ) as classifier:
            first = route_question('数据库课程主要讲什么')
            second = route_question('数据库课程主要讲什么')
        self.assertEqual(first['type'], 'rag')
        self.assertEqual(second['type'], 'rag')
        classifier.assert_called_once()


class QwenProviderTimeoutTests(SimpleTestCase):
    """短超时/重试次数必须透传到 HTTP 请求层。"""

    def test_chat_passes_timeout_and_retry_limit(self):
        provider = QwenProvider()
        provider.api_key = 'test-key'
        with patch('utils.llm_provider.ExternalClient.post') as post:
            post.return_value.json.return_value = {
                'choices': [{'message': {'content': 'ok'}}],
            }
            result = provider.chat(
                [{'role': 'user', 'content': 'hi'}],
                timeout=1.5,
                max_retries=1,
            )
        self.assertEqual(result, 'ok')
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs['timeout'], 1.5)


@override_settings(
    QUERY_ROUTER_LLM_ENABLED=False,
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
    },
)
class QueryRouterLLMDisabledTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_disabled_config_skips_llm(self):
        with patch('utils.query_router._call_llm_classifier') as classifier:
            route = route_question('数据库课程主要讲什么')
        self.assertEqual(route['type'], 'rag')
        classifier.assert_not_called()
