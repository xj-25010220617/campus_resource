"""对比今天各次"15级"提问的 parsed_filters：定位硬过滤清零的原因"""
import os
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

import json
from apps.chat.models import QueryLog

qs = (
    QueryLog.objects
    .filter(question__icontains='毕业生')
    .order_by('created_at')
)
print('今天所有毕业生相关提问:')
for log in qs:
    print('=' * 60)
    print('时间:', log.created_at, ' 用户:', log.user_id)
    print('问题:', log.question)
    print('hit_count:', log.hit_count, ' top_doc_ids:', log.top_doc_ids)
    try:
        print('parsed_filters:', json.dumps(log.parsed_filters, ensure_ascii=False))
    except Exception:
        print('parsed_filters:', log.parsed_filters)
