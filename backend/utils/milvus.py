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
    # 向量维度唯一事实源：settings.EMBED_VECTOR_DIM（由 EMBED_BACKEND 派生，可 env 覆盖）。
    # 禁止在各处硬编码维度——Embedding 后端与集合维度不一致时插入必然失败。
    VECTOR_DIM = getattr(settings, 'EMBED_VECTOR_DIM', 768)

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

    def get_collection_dim(self) -> Optional[int]:
        """读取现有 collection 的向量维度；集合不存在或读取失败时返回 None。

        供维度一致性校验使用（ensure_collection 守卫、reindex_all --if-empty 守卫）。
        """
        try:
            from pymilvus import Collection, utility

            if not utility.has_collection(self.COLLECTION_NAME):
                return None
            for field in Collection(name=self.COLLECTION_NAME).schema.fields:
                if field.name == 'embedding':
                    return int(field.params.get('dim', 0)) or None
        except Exception as e:
            logger.warning(f'读取 Milvus 集合维度失败: {e}')
        return None

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
                existing_dim = self.get_collection_dim()
                if existing_dim is not None and existing_dim != self.VECTOR_DIM:
                    # 集合是旧 Embedding 配置下创建的，直接插入会报
                    # "expected dim X, got Y"。明确拒绝并给出修复动作，
                    # 避免文档索引静默卡死在 embedding 阶段。
                    logger.error(
                        'Milvus 集合 "%s" 维度为 %d，与当前 Embedding 配置的 %d 维不一致；'
                        '请运行 python scripts/reindex_all.py 重建向量库',
                        self.COLLECTION_NAME, existing_dim, self.VECTOR_DIM,
                    )
                    return False
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
        返回 vector_id 列表
        """
        if not self.ensure_collection():
            # 集合不可用（连接失败 / 维度与 Embedding 配置不匹配）：快速失败，
            # 让 index_document_task 记录明确原因而不是插入时的裸 MilvusException。
            raise RuntimeError(
                'Milvus 集合不可用：连接失败，或集合维度与当前 Embedding 配置不一致'
                '（请运行 python scripts/reindex_all.py 重建向量库）'
            )
        if embeddings and len(embeddings[0]) != self.VECTOR_DIM:
            raise ValueError(
                f'Embedding 后端产出 {len(embeddings[0])} 维向量，与配置的集合维度 '
                f'{self.VECTOR_DIM} 维不一致：请检查 EMBED_BACKEND / EMBED_VECTOR_DIM 配置，'
                '或运行 python scripts/reindex_all.py 重建向量库'
            )
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
    def backend_name(self) -> str:
        """当前可用的向量后端，供健康检查 / 监控探测。只读，不建连接。

        Returns:
            'milvus'      —— 已连上 Milvus
            'chroma'      —— Milvus 不可用，但 ChromaDB 可导入（降级后端可用）
            'unavailable' —— 两者都不可用：向量检索**无法执行**
        """
        if self._connected:
            return 'milvus'
        try:
            import chromadb  # noqa: F401
        except ImportError:
            return 'unavailable'
        return 'chroma'

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        filter_ids: Optional[List[int]] = None,
    ) -> Optional[List[dict]]:
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
                —— 检索**执行成功**；空列表是合法的"确实没有匹配"
            None
                —— 向量后端**不可用**，检索根本没执行

        Note（P1-2 Phase 1-2 / 审计报告 P-7）
        -------------------------------------
        这两种结果**必须区分**。此前两者都返回 `[]`，于是"向量库挂了"在
        问答侧表现为"知识库里没有相关内容"——故障被伪装成正常的空结果，
        运维与用户都无法察觉。调用方拿到 `None` 时必须按"服务不可用"处理，
        绝不能 `or []` 吞掉。
        """
        if self._connected:
            # 已连接：直接检索。局部异常由 _search_milvus 内部降级为空结果
            # （空结果是合法结果——"确实没有匹配"，不能被当成后端故障）。
            return self._search_milvus(query_embedding, top_k, filter_ids)
        # 未连接：懒连接并确保集合可用（连接失败 / 集合创建失败 / 维度与
        # Embedding 配置不一致都在 ensure_collection 内被判定并记录）。
        # 集合不可用说明向量检索**根本没执行**：按 P-7 返回 None（不是空列表），
        # 交由上层区分"服务不可用"与"确实没有匹配"，并按关键词兜底继续。
        # ensure_collection() 自身捕获异常，不会把 SchemaNotReadyException
        # 等裸异常抛给调用方。
        if not self.ensure_collection():
            return None
        return self._search_milvus(query_embedding, top_k, filter_ids)

    def _search_milvus(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_ids: Optional[List[int]],
    ) -> List[dict]:
        from pymilvus import Collection
        try:
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
        except Exception as e:
            # 检索异常（collection 不存在/未加载等）降级为空结果，不阻断上层关键词兜底
            logger.warning('Milvus 搜索失败，降级为空结果: %s', e)
            return []

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
