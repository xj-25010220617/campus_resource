"""在服务器上测试问答接口，获取完整 traceback"""
import sys
import traceback
import requests

BASE = 'http://127.0.0.1:3000'

# 方法1: 直接用 requests 请求
print('=== Method 1: HTTP request ===')
try:
    resp = requests.post(
        f'{BASE}/api/chat/ask/',
        json={'question': '你好'},
        timeout=120,
    )
    print(f'STATUS: {resp.status_code}')
    print(f'CONTENT-TYPE: {resp.headers.get("Content-Type")}')
    print(f'X-Trace-Id: {resp.headers.get("X-Trace-Id")}')
    print('---BODY---')
    print(resp.text[:3000])
    print('---BODY END---')
except Exception as e:
    print(f'REQUEST ERROR: {type(e).__name__}: {e}')
    traceback.print_exc()

# 方法2: Django shell 内部调用（跳过 HTTP 层，拿 traceback）
print('\n=== Method 2: Django internal call ===')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/www/wwwroot/campus_resource/backend')

import django
django.setup()

from django.test import RequestFactory
from apps.chat.views import AskView

factory = RequestFactory()
request = factory.post('/api/chat/ask/', {'question': '你好'}, content_type='application/json')
view = AskView.as_view()

try:
    response = view(request)
    print(f'STATUS: {response.status_code}')
    if hasattr(response, 'streaming_content'):
        content = b''
        for chunk in response.streaming_content:
            content += chunk
            if content.count(b'\n\n') >= 3:
                break
        print(f'SSE CONTENT: {content.decode("utf-8", errors="replace")[:2000]}')
    elif hasattr(response, 'content'):
        print(f'CONTENT: {response.content.decode("utf-8", errors="replace")[:2000]}')
except Exception as e:
    print(f'EXCEPTION: {type(e).__name__}: {e}')
    traceback.print_exc()
