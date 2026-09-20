"""
Milvus 向量数据库封装
- Collection 创建与管理
- 文档 chunk 向量化入库
- 向量相似度搜索
"""
import logging
from typing import List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


class MilvusClient:
    """
    Milvus 向量库客户端。
    唯一向量事实源；首次向量操作时懒连接，失败自动降级重试。
    """

    COLLECTION_NAME = 'campus_knowledge'
    VECTOR_DIM = 768  # bge-base-zh-v1.5 维度

    def __init__(self):
        self._connected = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """连接 Milvus Lite（幂等）。失败保持未连接状态，下次调用重试。"""
        if self._connected:
            return True
        try:
            from pymilvus import connections
            connections.connect(
                alias='default',
                uri=str(getattr(settings, 'MILVUS_URI', '.campus_milvus.db')),
            )
            self._connected = True
            logger.info('Milvus connected')
            return True
        except ImportError:
            logger.warning('pymilvus not installed')
            self._connected = False
            return False
        except Exception as e:
            logger.warning(f'Milvus connection failed: {e}')
            self._connected = False
            return False

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------
    def ensure_collection(self) -> bool:
        if not self._connected:
            self.connect()
        return self._ensure_milvus_collection()

    def _ensure_milvus_collection(self) -> bool:
        try:
            from pymilvus import Collection, CollectionSchema, FieldSchema, DataType, utility

            if utility.has_collection(self.COLLECTION_NAME):
                return True

            fields = [
                FieldSchema(name='id', dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name='vector_id', dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name='doc_id', dtype=DataType.INT64),
                FieldSchema(name='chunk_index', dtype=DataType.INT32),
                FieldSchema(name='chunk_text', dtype=DataType.VARCHAR, max_length=8192),
                FieldSchema(name='page_number', dtype=DataType.INT32),
                FieldSchema(name='seq_order', dtype=DataType.INT32),
                FieldSchema(name='embedding', dtype=DataType.FLOAT_VECTOR, dim=self.VECTOR_DIM),
            ]
            schema = CollectionSchema(fields, description='校园知识库向量集合')
            collection = Collection(name=self.COLLECTION_NAME, schema=schema)

            # BRUTE_FORCE：精确检索（无近似，100% 召回），无需 faiss；
            # 数据量 ~3k 量级时比 IVF_FLAT 更快且更准。
            index_params = {
                'metric_type': 'COSINE',
                'index_type': 'BRUTE_FORCE',
                'params': {},
            }
            collection.create_index(field_name='embedding', index_params=index_params)
            collection.load()
            logger.info(f'Milvus collection "{self.COLLECTION_NAME}" created')
            return True
        except Exception as e:
            logger.error(f'Failed to create Milvus collection: {e}')
            return False

    # ------------------------------------------------------------------
    # 向量入库
    # ------------------------------------------------------------------
    def insert_chunks(self, chunks: List[dict], embeddings: List[List[float]]) -> List[str]:
        """
        批量插入 chunk 向量。
        参数:
            chunks: [{'vector_id': str, 'doc_id': int, 'chunk_index': int,
                      'chunk_text': str, 'page_number': int, 'seq_order': int}, ...]
            embeddings: [[0.1, 0.2, ...], ...]
        返回:
            vector_id 列表
        """
        if not self._connected:
            self.connect()
        return self._insert_milvus(chunks, embeddings)

    def _insert_milvus(self, chunks: List[dict], embeddings: List[List[float]]) -> List[str]:
        from pymilvus import Collection
        collection = Collection(name=self.COLLECTION_NAME)
        data = [
            [c['vector_id'] for c in chunks],
            [c['doc_id'] for c in chunks],
            [c['chunk_index'] for c in chunks],
            [c.get('chunk_text', '') for c in chunks],
            [c.get('page_number', 0) for c in chunks],
            [c.get('seq_order', 0) for c in chunks],
            embeddings,
        ]
        collection.insert(data)
        collection.flush()
        return [c['vector_id'] for c in chunks]

    # ------------------------------------------------------------------
    # 向量搜索
    # ------------------------------------------------------------------
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_ids: Optional[List[int]] = None,
    ) -> List[dict]:
        """
        向量相似度搜索（支持按 doc_id 范围硬过滤）。

        Args:
            query_embedding: 查询向量
            top_k: 返回数量
            filter_ids: 限定 doc_id 范围（来自 SQL 硬过滤，为空/None 时不过滤）

        Returns:
            [{'vector_id': str, 'doc_id': int, 'chunk_index': int,
              'chunk_text': str, 'page_number': int, 'seq_order': int,
              'score': float}, ...]
        """
        if not self._connected:
            self.connect()
        return self._search_milvus(query_embedding, top_k, filter_ids)

    def _search_milvus(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_ids: Optional[List[int]],
    ) -> List[dict]:
        from pymilvus import Collection
        collection = Collection(name=self.COLLECTION_NAME)
        collection.load()

        expr = None
        if filter_ids:
            valid_ids = [d for d in filter_ids if d > 0]
            if valid_ids:
                expr = f'doc_id in {valid_ids}'

        results = collection.search(
            data=[query_embedding],
            anns_field='embedding',
            param={'metric_type': 'COSINE'},
            limit=top_k,
            expr=expr,
            output_fields=['vector_id', 'doc_id', 'chunk_index',
                           'chunk_text', 'page_number', 'seq_order'],
        )
        hits = []
        for hit in results[0]:
            hits.append({
                'vector_id': hit.entity.get('vector_id'),
                'doc_id': hit.entity.get('doc_id'),
                'chunk_index': hit.entity.get('chunk_index'),
                'chunk_text': hit.entity.get('chunk_text', ''),
                'page_number': hit.entity.get('page_number', 0),
                'seq_order': hit.entity.get('seq_order', 0),
                'score': hit.score,
            })
        return hits

    # ------------------------------------------------------------------
    # 删除向量
    # ------------------------------------------------------------------
    def delete_by_doc(self, doc_id: int) -> bool:
        """删除某文档的所有向量"""
        if not self._connected:
            self.connect()
        if not self._connected:
            return False
        from pymilvus import Collection
        collection = Collection(name=self.COLLECTION_NAME)
        collection.delete(f'doc_id == {doc_id}')
        return True


# 全局单例
milvus_client = MilvusClient()
