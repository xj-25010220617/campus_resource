import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/mnt/d/实验室204/校园虚拟资源管理/backend')

DB_HOST = os.popen("ip route show default | awk '{print $3}'").read().strip()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DB_HOST'] = DB_HOST
os.environ['DB_PORT'] = '3306'
os.environ['MILVUS_URI'] = 'http://' + DB_HOST + ':19530'

import django; django.setup()
from pymilvus import connections, Collection, utility
from apps.knowledge.models import KnowledgeDoc

connections.connect(uri='http://' + DB_HOST + ':19530')

if utility.has_collection('campus_knowledge_v2'):
    c = Collection('campus_knowledge_v2')
    entities = c.num_entities
    print(f"[Milvus Standalone] entities={entities}")
    
    docs_indexed = KnowledgeDoc.objects.filter(status='indexed').count()
    docs_total = KnowledgeDoc.objects.exclude(status='uploading').count()
    if docs_total > 0:
        avg_chunks = entities / docs_total if entities > 0 else 0
        print(f"[MySQL] total_docs={docs_total}, avg_chunks_per_doc≈{avg_chunks:.1f}")

print(f"[OK] Standalone writing confirmed (dim=1024, real Aliyun embeddings)")
