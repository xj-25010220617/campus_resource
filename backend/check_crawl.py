import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, "/www/wwwroot/campus_resource/backend")
import django
django.setup()

from apps.knowledge.models import KnowledgeDoc, DocChunk

total = KnowledgeDoc.objects.count()
website = KnowledgeDoc.objects.filter(source_type='website').count()
indexed_web = KnowledgeDoc.objects.filter(source_type='website', status='indexed').count()
pending_web = KnowledgeDoc.objects.filter(source_type='website', status='pending').count()
chunks = DocChunk.objects.count()

print(f"=== 官网爬虫结果 ===")
print(f"  总文档: {total}")
print(f"  website 新入库: {website}")
print(f"  website indexed: {indexed_web}")
print(f"  website pending（等待索引）: {pending_web}")
print(f"  总切片数: {chunks}")

print(f"\n=== 最新 website 文档 ===")
for d in KnowledgeDoc.objects.filter(source_type='website').order_by('-created_at')[:10]:
    print(f"  [{d.status:10s}] {d.title[:60]}")

print(f"\n=== 检查 Milvus 有多少 website 切片 ===")
try:
    from django.conf import settings
    from pymilvus import connections, Collection
    connections.connect("default", host=getattr(settings, 'MILVUS_HOST', '127.0.0.1'), port=str(getattr(settings, 'MILVUS_PORT', 19530)))
    c = Collection(getattr(settings, 'MILVUS_COLLECTION', 'knowledge_chunks'))
    print(f"  Milvus 集合实体数: {c.num_entities}")
except Exception as e:
    print(f"  Milvus 检查跳过: {e}")
