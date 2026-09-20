"""
Milvus Collection 重建脚本

用途：从 1536 维（旧 text-embedding-v2）迁移到 1024 维（text-embedding-v3）
操作：删除旧 collection，重新创建空 collection（旧向量数据会丢失，需重新导入文档）

用法：
  python manage.py shell < backend/scripts/rebuild_milvus.py
  或
  cd backend && python -c "exec(open('scripts/rebuild_milvus.py').read())"
"""
import os
import sys

# 确保 Django settings 可用
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from utils.milvus import milvus_client
from pymilvus import connections, Collection


def rebuild_collection():
    COLLECTION_NAME = 'campus_knowledge'

    print(f'正在连接 Milvus ...')
    milvus_client.connect()

    if not milvus_client.connected:
        print('ERROR: Milvus 连接失败，请检查 MILVUS_URI 路径及 milvus-lite 安装')
        return

    print(f'检查 collection "{COLLECTION_NAME}" ...')
    if Collection.exists(COLLECTION_NAME):
        print(f'  发现旧 collection，准备删除 ...')
        try:
            Collection(COLLECTION_NAME).drop()
            print(f'  已删除旧 collection')
        except Exception as e:
            print(f'ERROR: 删除旧 collection 失败: {e}')
            return
    else:
        print(f'  collection 不存在，无需删除')

    print(f'创建新 collection（维度 = {milvus_client.VECTOR_DIM}）...')
    success = milvus_client.ensure_collection()
    if success:
        print(f'SUCCESS: 新 collection 创建成功')
    else:
        print(f'ERROR: 新 collection 创建失败')


if __name__ == '__main__':
    rebuild_collection()
