import sys
import types
from unittest import mock

import numpy as np
from django.test import SimpleTestCase


class TestEmbedding(SimpleTestCase):
    def test_vector_dim_768(self):
        from utils.llm import LLMClient
        assert LLMClient.VECTOR_DIM == 768

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
