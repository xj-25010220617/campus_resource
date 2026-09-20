"""
全量重索引脚本：重建 Milvus Collection 并对所有知识文档重新索引。

用途：向量库损坏/维度迁移/Embedding 模型切换后一键重建。
会清空旧向量（含孤儿向量），再逐文档重新解析 → 分块 → Embedding → 入库。

用法：
    cd backend && python scripts/reindex_all.py

流程：
    1. 连接 Milvus（失败即退出）
    2. drop 旧 collection 并重建空 collection（清空孤儿向量）
    3. 遍历所有 KnowledgeDoc（排除正在上传的会话态），同步调用
       index_document_task 重新索引
    4. 打印进度与结果汇总
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.db.models import Q

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# 减少第三方库日志噪音
logging.getLogger('pymilvus').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


def _reindex_doc(doc_id: int):
    """同步调用 index_document_task，兼容 Celery(bind=True) 与本地降级两种模式。"""
    from apps.knowledge.tasks import index_document_task

    # Celery 模式：task.run(None, doc_id) 同步执行；降级模式无 .run，走 .delay
    if hasattr(index_document_task, 'run'):
        return index_document_task.run(None, doc_id)
    return index_document_task.delay(doc_id)


def rebuild_collection() -> bool:
    """drop 并重建 collection，清空所有旧向量（含孤儿向量）。"""
    from pymilvus import utility

    from utils.milvus import milvus_client

    milvus_client.connect()
    if not milvus_client.connected:
        logger.error('Milvus 连接失败，请确认 MILVUS_URI 路径可写及 milvus-lite 已安装')
        return False

    name = milvus_client.COLLECTION_NAME
    if utility.has_collection(name):
        logger.info('发现旧 collection "%s"，准备删除...', name)
        try:
            utility.drop_collection(name)
            logger.info('已删除旧 collection')
        except Exception as e:
            logger.error('删除旧 collection 失败: %s', e)
            return False

    ok = milvus_client.ensure_collection()
    if not ok:
        logger.error('新建 collection 失败')
        return False
    logger.info('新 collection 就绪（dim=%s）', milvus_client.VECTOR_DIM)
    return True


def reindex_all():
    from apps.knowledge.models import KnowledgeDoc

    # 状态分布
    from django.db.models import Count
    dist = dict(
        KnowledgeDoc.objects.values_list('status').annotate(c=Count('id'))
    )
    total = sum(dist.values())
    print(f'\n{"="*55}')
    print('  全量重索引')
    print(f'{"="*55}')
    print(f'  总文档: {total}')
    for status, c in sorted(dist.items()):
        print(f'    {status}: {c}')
    print(f'{"="*55}\n')

    if total == 0:
        print('没有文档，退出。')
        return

    # 排除正在上传的会话态（若存在）；其余一律重索引（含 failed/已索引）
    docs = KnowledgeDoc.objects.filter(~Q(status='uploading')).order_by('id')
    n = docs.count()

    success_count = 0
    fail_count = 0
    t0 = time.time()

    for i, doc in enumerate(docs, 1):
        print(f'[{i}/{n}] 重索引 #{doc.id}: "{doc.title[:50]}"...', end=' ', flush=True)
        start = time.time()
        try:
            _reindex_doc(doc.id)
            doc.refresh_from_db()
            elapsed = time.time() - start
            if doc.status == 'indexed':
                print(f'OK {elapsed:.1f}s (chunks={doc.chunk_count})')
                success_count += 1
            else:
                print(f'FAIL {elapsed:.1f}s (status={doc.status})')
                fail_count += 1
        except Exception as e:
            elapsed = time.time() - start
            print(f'FAIL {elapsed:.1f}s — {type(e).__name__}: {e}')
            fail_count += 1

    total_elapsed = time.time() - t0
    print(f'\n{"="*55}')
    print(f'  重索引完成!')
    print(f'  耗时: {total_elapsed:.1f}s')
    print(f'  成功: {success_count}')
    print(f'  失败: {fail_count}')
    print(f'{"="*55}')


if __name__ == '__main__':
    if rebuild_collection():
        reindex_all()
