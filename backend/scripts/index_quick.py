"""快速索引测试：只索引前3个文档"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.getLogger('chromadb').setLevel(logging.WARNING)

from utils.milvus import milvus_client
print('1. 连接向量数据库...')
milvus_client.connect()
milvus_client.ensure_collection()
print('2. 向量库就绪')

from apps.knowledge.models import KnowledgeDoc
from apps.knowledge.tasks import index_document_task

uploaded = KnowledgeDoc.objects.filter(status='uploaded')
print(f'3. 待索引: {uploaded.count()} 个文档')

for doc in uploaded[:3]:
    print(f'   索引 #{doc.id}: {doc.title[:40]}...', end=' ')
    try:
        index_document_task.run(None, doc.id)
        doc.refresh_from_db()
        print(f'-> {doc.status}')
    except Exception as e:
        print(f'FAIL: {e}')

print('4. 完成')
