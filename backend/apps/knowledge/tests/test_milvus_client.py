from unittest import mock

from django.test import SimpleTestCase


class TestMilvusLazyConnect(SimpleTestCase):
    def test_connect_idempotent(self):
        from utils.milvus import MilvusClient

        c = MilvusClient()
        assert c.connected is False
        with mock.patch('pymilvus.connections.connect', return_value=None):
            assert c.connect() is True
            assert c.connected is True
            # 再次调用不再重复 connect
            with mock.patch('pymilvus.connections.connect') as m:
                assert c.connect() is True
                m.assert_not_called()

    def test_lazy_connect_on_search(self):
        from utils.milvus import MilvusClient

        c = MilvusClient()
        # search() 现在先走 ensure_collection()（内部含懒连接 + 集合就绪校验），
        # 这里 mock 掉它以避免测试依赖真实 Milvus 连接；懒连接行为由
        # test_connect_idempotent 覆盖。
        with mock.patch.object(c, 'ensure_collection', return_value=True) as mec:
            with mock.patch.object(c, '_search_milvus', return_value=[]) as ms:
                c.search([0.1] * 768, top_k=3)
                mec.assert_called_once()
                ms.assert_called_once()
