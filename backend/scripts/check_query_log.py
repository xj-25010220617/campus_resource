"""服务器端诊断：查用户真实提问的 query_log + jieba 依赖状态"""
import os
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

# 1. jieba 是否安装
try:
    import jieba
    print('jieba: 已安装', list(jieba.cut('15级优秀毕业生')))
except ImportError:
    print('jieba: 未安装!')

# 2. 最近的毕业生相关真实提问记录
from apps.chat.models import QueryLog
qs = (
    QueryLog.objects
    .filter(question__icontains='毕业生')
    .order_by('-created_at')[:10]
)
print()
print('最近的毕业生相关提问记录:', qs.count())
for log in qs:
    print('=' * 60)
    print('时间:', log.created_at)
    print('问题:', log.question)
    print('hit_count:', log.hit_count)
    print('top_doc_ids:', log.top_doc_ids)
    print('response_ms:', log.response_ms)
    print('parsed_filters:', log.parsed_filters)
