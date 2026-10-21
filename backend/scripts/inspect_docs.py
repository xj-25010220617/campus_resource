"""取证：最近的 RAG 监控日志 + top_doc_ids 对应文档标题"""
import os
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from apps.knowledge.models import KnowledgeDoc

print('==== top_doc_ids 对应文档 ====')
for did in [363, 352, 350, 349, 343, 314]:
    d = KnowledgeDoc.objects.filter(id=did).values_list(
        'title', 'file_name', 'file_type', 'status', 'visibility',
    ).first()
    print(did, '->', d)
