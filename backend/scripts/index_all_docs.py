"""
批量索引脚本：将所有 status='uploaded' 的知识文档索引入库。

用法：
    python scripts/index_all_docs.py

流程（绕过 Celery，直接同步执行）：
    1. 遍历 KnowledgeDoc 中 status='uploaded' 的记录
    2. 初始化向量数据库（Milvus → ChromaDB 降级）
    3. 对每个文档调用 index_document_task 同步索引
    4. 打印进度和结果汇总
"""
import logging
import os
import sys
import time

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.conf import settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# 减少第三方库的日志噪音
logging.getLogger('chromadb').setLevel(logging.WARNING)
logging.getLogger('pymilvus').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


def ensure_vector_db():
    """确保向量数据库就绪（优先 Milvus，降级 ChromaDB）"""
    from utils.milvus import milvus_client

    logger.info('连接向量数据库...')
    connected = milvus_client.connect()
    if connected:
        logger.info('Milvus 已连接')
    else:
        logger.warning('Milvus 不可用，使用 ChromaDB 降级')

    logger.info('确保 Collection 就绪...')
    ok = milvus_client.ensure_collection()
    if ok:
        logger.info('向量数据库就绪')
    else:
        logger.error('向量数据库初始化失败，无法继续')
        sys.exit(1)

    return milvus_client


def index_all_docs():
    """遍历并索引所有 uploaded 文档"""
    from apps.knowledge.models import KnowledgeDoc
    from apps.knowledge.tasks import index_document_task

    # 统计状态分布
    total = KnowledgeDoc.objects.count()
    uploaded = KnowledgeDoc.objects.filter(status='uploaded').count()
    indexed = KnowledgeDoc.objects.filter(status='indexed').count()
    failed = KnowledgeDoc.objects.filter(status='failed').count()
    other = total - uploaded - indexed - failed

    print(f'\n{"="*55}')
    print(f'  知识库索引工具')
    print(f'{"="*55}')
    print(f'  总文档:      {total}')
    print(f'  待索引:      {uploaded} (status=uploaded)')
    print(f'  已索引:      {indexed}')
    print(f'  失败:        {failed}')
    print(f'  其他状态:    {other}')
    print(f'{"="*55}\n')

    if uploaded == 0:
        print('没有待索引的文档，程序退出。')
        return

    # 确认
    print(f'即将索引 {uploaded} 个文档...')
    print()

    docs = KnowledgeDoc.objects.filter(status='uploaded').order_by('id')

    success_count = 0
    fail_count = 0
    total_start = time.time()

    for i, doc in enumerate(docs, 1):
        print(f'[{i}/{uploaded}] 索引文档 #{doc.id}: "{doc.title[:50]}"...', end=' ', flush=True)
        doc_start = time.time()

        try:
            # 同步调用 index_document_task（使用 .run() 绕过 Celery __call__）
            index_document_task.run(None, doc.id)
            elapsed = time.time() - doc_start

            # 重新读取确认状态
            doc.refresh_from_db()
            if doc.status == 'indexed':
                print(f'✓ {elapsed:.1f}s (chunks={doc.chunk_count})')
                success_count += 1
            else:
                print(f'✗ {elapsed:.1f}s (status={doc.status})')
                fail_count += 1
        except Exception as e:
            elapsed = time.time() - doc_start
            print(f'✗ {elapsed:.1f}s — {type(e).__name__}: {e}')
            fail_count += 1

    total_elapsed = time.time() - total_start
    print(f'\n{"="*55}')
    print(f'  索引完成!')
    print(f'  耗时: {total_elapsed:.1f}s')
    print(f'  成功: {success_count}')
    print(f'  失败: {fail_count}')
    print(f'{"="*55}')


if __name__ == '__main__':
    # 1. 确保向量数据库就绪
    ensure_vector_db()

    # 2. 执行索引
    index_all_docs()
