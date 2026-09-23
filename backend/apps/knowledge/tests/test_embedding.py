import sys
import types
from unittest import mock

import numpy as np
from django.test import SimpleTestCase


class TestEmbedding(SimpleTestCase):
    def test_vector_dim_matches_settings(self):
        """VECTOR_DIM 必须与 settings.EMBED_VECTOR_DIM（由 EMBED_BACKEND 派生）一致。

        历史教训：维度曾在 milvus.py / llm.py 各自硬编码 768，与服务器实际使用的
        阿里云 text-embedding-v3（1024 维）不一致，导致全部插入报
        MilvusException: expected dim 768, got 1024，文档索引静默卡死。
        """
        from django.conf import settings

        from utils.llm import LLMClient
        from utils.milvus import MilvusClient

        assert LLMClient.VECTOR_DIM == settings.EMBED_VECTOR_DIM
        assert MilvusClient.VECTOR_DIM == settings.EMBED_VECTOR_DIM

    def test_query_prefix_constant(self):
        from utils.llm import BGE_QUERY_PREFIX
        assert BGE_QUERY_PREFIX == '为这个句子生成表示以用于检索相关文章：'

    def test_query_prefix_added_passage_not(self):
        from utils.llm import BGE_QUERY_PREFIX, LLMClient

        fake_st = types.ModuleType('sentence_transformers')
        fake_model = mock.MagicMock()
        fake_model.encode.return_value = np.zeros((1, 768))
        fake_st.SentenceTransformer = mock.MagicMock(return_value=fake_model)

        client = LLMClient()
        with mock.patch.dict(sys.modules, {'sentence_transformers': fake_st}):
            # query 侧加前缀
            client._embed_via_local(['校园网怎么登录'], is_query=True)
            arg = fake_model.encode.call_args[0][0]
            assert arg[0].startswith(BGE_QUERY_PREFIX)

        fake_model.reset_mock()
        with mock.patch.dict(sys.modules, {'sentence_transformers': fake_st}):
            # passage 侧不加前缀
            client._embed_via_local(['校园网怎么登录'], is_query=False)
            arg = fake_model.encode.call_args[0][0]
            assert not arg[0].startswith(BGE_QUERY_PREFIX)
