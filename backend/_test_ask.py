import requests, re

login = requests.post('http://127.0.0.1:8000/api/auth/sso/callback/', json={'code': 'mock_admin'})
token = login.json()['access']

resp = requests.post(
    'http://127.0.0.1:8000/api/chat/ask/',
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
    json={'question': '操作系统'},
    timeout=30,
    stream=True,
)
html = b''.join(resp.iter_content()).decode('utf-8', errors='replace')

# 提取异常类型
exc_match = re.search(r'<title>(.*?)</title>', html)
print('title:', exc_match.group(1) if exc_match else '未知')

# 提取异常值
tb_match = re.search(r'class="exception_value">(.*?)</pre>', html, re.DOTALL)
if tb_match:
    print('异常值:', tb_match.group(1).strip()[:200])

# 找关键 traceback 行
lines = html.split('\n')
for line in lines:
    stripped = line.strip()
    if stripped.startswith('File') and ('chat' in stripped or 'rag' in stripped or 'milvus' in stripped or 'query_parser' in stripped):
        print('相关行:', stripped[:300])
    if stripped.startswith('Exception Value:'):
        print('异常值2:', stripped[:300])
