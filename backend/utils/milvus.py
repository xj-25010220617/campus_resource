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
    开发阶段可用 ChromaDB 替代降级运行。
    """

    COLLECTION_NAME = 'campus_knowledge'
    VECTOR_DIM = 1024  # text-embedding-v3 维度（与 llm.py _mock_embed 保持一致）

    def __init__(self):
        self._connected = False

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """连接 Milvus，失败时降级为 ChromaDB"""
        try:
            from pymilvus import connections
            connections.connect(
                alias='default',
                host=getattr(settings, 'MILVUS_HOST', 'localhost'),
                port=getattr(settings, 'MILVUS_PORT', '19530'),
            )
            self._connected = True
            logger.info('Milvus connected')
            return True
        except ImportError:
            logger.warning('pymilvus not installed, falling back to ChromaDB')
            self._connected = False
            return False
        except Exception as e:
            logger.warning(f'Milvus connection failed: {e}, falling back to ChromaDB')
            self._connected = False
            return False

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------
    def ensure_collection(self) -> bool:
        """确保 Collection 存在，不存在则创建"""
        if self._connected:
            return self._ensure_milvus_collection()
        return self._ensure_chroma_collection()

    def _ensure_milvus_collection(self) -> bool:
        try:
            from pymilvus import Collection, CollectionSchema, FieldSchema, DataType

            if Collection.exists(self.COLLECTION_NAME):
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

            # 创建 IVF_FLAT 索引
            index_params = {
                'metric_type': 'COSINE',
                'index_type': 'IVF_FLAT',
                'params': {'nlist': 128},
            }
            collection.create_index(field_name='embedding', index_params=index_params)
            collection.load()
            logger.info(f'Milvus collection "{self.COLLECTION_NAME}" created')
            return True
        except Exception as e:
            logger.error(f'Failed to create Milvus collection: {e}')
            return False

    def _ensure_chroma_collection(self) -> bool:
        """ChromaDB 降级方案"""
        try:
            import chromadb
            client = chromadb.PersistentClient(
                path=str(getattr(settings, 'CHROMA_DB_PATH', './chroma_db'))
            )
            client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={'hnsw:space': 'cosine'},
            )
            logger.info('ChromaDB collection ready')
            return True
        except ImportError:
            logger.error('chromadb not installed')
            return False
        except Exception as e:
            logger.error(f'ChromaDB setup failed: {e}')
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
        if self._connected:
            return self._insert_milvus(chunks, embeddings)
        return self._insert_chroma(chunks, embeddings)

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

    def _insert_chroma(self, chunks: List[dict], embeddings: List[List[float]]) -> List[str]:
        import chromadb
        client = chromadb.PersistentClient(
            path=str(getattr(settings, 'CHROMA_DB_PATH', './chroma_db'))
        )
        ids = [c['vector_id'] for c in chunks]
        metadatas = [
            {
                'doc_id': c['doc_id'],
                'chunk_index': c['chunk_index'],
                'page_number': c.get('page_number', 0),
                'seq_order': c.get('seq_order', 0),
            }
            for c in chunks
        ]
        # ChromaDB 存储文档文本
        documents = [c.get('chunk_text', '') for c in chunks]
        collection = client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={'hnsw:space': 'cosine'},
        )
        collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        return ids

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
        if self._connected:
            return self._search_milvus(query_embedding, top_k, filter_ids)
        return self._search_chroma(query_embedding, top_k, filter_ids)

    def _search_milvus(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_ids: Optional[List[int]],
    ) -> List[dict]:
        from pymilvus import Collection
        collection = Collection(name=self.COLLECTION_NAME)
        collection.load()

        # 硬过滤：只检索指定 doc_id 范围内的向量（来自 SQL 硬过滤）
        expr = None
        if filter_ids:
            # doc_id 与 KnowledgeDoc 主键保持一致。
            valid_ids = [d for d in filter_ids if d > 0]
            if valid_ids:
                expr = f'doc_id in {valid_ids}'

        results = collection.search(
            data=[query_embedding],
            anns_field='embedding',
            param={'metric_type': 'COSINE', 'params': {'nprobe': 10}},
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

    def _search_chroma(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_ids: Optional[List[int]],
    ) -> List[dict]:
        try:
            import chromadb
        except ImportError:
            logger.warning('chromadb 未安装，Chroma 向量检索不可用')
            return []
        client = chromadb.PersistentClient(
            path=str(getattr(settings, 'CHROMA_DB_PATH', './chroma_db'))
        )
        collection = client.get_collection(name=self.COLLECTION_NAME)

        # 检索时若限定了 doc_id 范围，过滤后可能数量不足，
        # 因此多取 5 倍以保证在过滤后仍有 top_k 条命中
        fetch_k = top_k * 5 if filter_ids else top_k
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k,
        )

        hits = []
        if results and results['ids'] and results['ids'][0]:
            filter_set = set(filter_ids) if filter_ids else None
            for i, vid in enumerate(results['ids'][0]):
                meta = results['metadatas'][0][i] if results.get('metadatas') else {}
                docs_list = results.get('documents')
                doc_text = docs_list[0][i] if docs_list and docs_list[0] else ''
                doc_id = meta.get('doc_id')

                # doc_id 硬过滤（跨版本兼容性兜底）
                if filter_set is not None and doc_id not in filter_set:
                    continue

                hits.append({
                    'vector_id': vid,
                    'doc_id': doc_id,
                    'chunk_index': meta.get('chunk_index'),
                    'chunk_text': doc_text,
                    'page_number': meta.get('page_number', 0),
                    'seq_order': meta.get('seq_order', 0),
                    'score': 1 - (results['distances'][0][i] if results.get('distances') else 0),
                })
        return hits

    # ------------------------------------------------------------------
    # 删除向量
    # ------------------------------------------------------------------
    def delete_by_doc(self, doc_id: int) -> bool:
        """删除某文档的所有向量"""
        if self._connected:
            from pymilvus import Collection
            collection = Collection(name=self.COLLECTION_NAME)
            collection.delete(f'doc_id == {doc_id}')
            return True
        return True  # ChromaDB 不支持按条件删，后续重建即可


# 全局单例
milvus_client = MilvusClient()
