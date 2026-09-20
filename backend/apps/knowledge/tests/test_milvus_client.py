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
        with mock.patch.object(c, 'connect', return_value=True) as mc:
            with mock.patch.object(c, '_search_milvus', return_value=[]) as ms:
                c.search([0.1] * 768, top_k=3)
                mc.assert_called_once()
                ms.assert_called_once()
