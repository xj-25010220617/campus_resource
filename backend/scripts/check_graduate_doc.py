"""服务器端诊断：查“15级优秀毕业生”相关文档的解析/索引状态"""
import os
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from apps.knowledge.models import KnowledgeDoc, DocChunk

conditions = ['毕业生', '15级', '15 级']
qs = KnowledgeDoc.objects.all()
from django.db.models import Q
q = Q()
for kw in conditions:
    q |= Q(title__icontains=kw)
    q |= Q(file_name__icontains=kw)
    q |= Q(description__icontains=kw)
docs = qs.filter(q)
print('匹配文档数:', docs.count())
for d in docs:
    print('=' * 60)
    print('id:', d.id)
    print('title:', d.title)
    print('file_name:', d.file_name)
    print('file_type:', d.file_type)
    print('status:', d.status)
    print('chunk_count:', d.chunk_count)
    print('indexed_at:', d.indexed_at)
    print('index_error:', (d.index_error or '')[:400])
    if hasattr(d, 'index_diagnostics'):
        print('index_diagnostics:', (getattr(d, 'index_diagnostics', '') or '')[:400])
    print('created_at:', d.created_at)
    # 分块正文预览
    chunks = DocChunk.objects.filter(doc=d).order_by('chunk_index')
    print('DocChunk 数:', chunks.count())
    for c in chunks[:50]:
        print(f'  chunk {c.chunk_index} ({len(c.content)}字符): {c.content[:60]!r}')
