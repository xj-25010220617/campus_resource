"""端到端验证：真实调 /api/chat/ask/ 问"15级优秀毕业生有谁"，
统计 LLM 回答对 doc 314 源文件名单的覆盖率。
"""
import json
import os
import re
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

import requests
from rest_framework_simplejwt.tokens import RefreshToken
from apps.accounts.models import User
from apps.knowledge.models import DocChunk

# 1. 管理员 token
admin = User.objects.filter(is_superuser=True).first() or User.objects.filter(role='super_admin').first()
token = str(RefreshToken.for_user(admin).access_token)
print('用户:', admin.username, '角色:', admin.role)

# 2. 源文件名单（doc 314 全部分块）
chunks = DocChunk.objects.filter(doc_id=314).order_by('chunk_index')
src_text = '\n'.join(c.content for c in chunks)
name_re = re.compile(r'^([\u4e00-\u9fa5]{2,4})[\s\d]', re.M)
src_names = []
for m in name_re.finditer(src_text):
    n = m.group(1)
    if n not in src_names:
        src_names.append(n)
print(f'源文件疑似人名: {len(src_names)} 个')

# 3. 调真实接口（SSE）
resp = requests.post(
    'http://127.0.0.1:3000/api/chat/ask/',
    json={'question': '15级优秀毕业生有谁'},
    headers={'Authorization': f'Bearer {token}'},
    stream=True, timeout=240,
)
print('HTTP', resp.status_code)
resp.encoding = 'utf-8'
answer = ''
refs = None
for line in resp.iter_lines(decode_unicode=True):
    if not line or not line.startswith('data:'):
        continue
    try:
        data = json.loads(line[5:].strip())
    except Exception:
        continue
    if isinstance(data, dict):
        if 'content' in data:
            answer += data['content']
        elif 'count' in data and 'summary' in data:
            refs = data
        elif 'message' in data and 'code' in data:
            print('!! error 事件:', data)

print('回答长度:', len(answer), '字符')
print('引用 summary:', refs)
missing = [n for n in src_names if n not in answer]
print(f'覆盖: {len(src_names) - len(missing)}/{len(src_names)}')
if missing:
    print('未出现的人名（前 30 个）:', '、'.join(missing[:30]))
print('---- 回答开头 300 字 ----')
print(answer[:300])
print('---- 回答结尾 200 字 ----')
print(answer[-200:])

# 4. query_log 记录
from apps.chat.models import QueryLog
log = QueryLog.objects.filter(question__contains='15级').order_by('-created_at').first()
if log:
    print('最新 QueryLog: hit_count=', log.hit_count, ' top_doc_ids=', log.top_doc_ids[:15])
