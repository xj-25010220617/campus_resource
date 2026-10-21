"""修复文件名 + 重新下载缺失 PDF。"""
import subprocess

SSH = ['ssh', '-i', r'D:\实验室204\校园虚拟资源管理\campus_server_ubuntu_ed25519',
       '-o', 'StrictHostKeyChecking=no', 'ubuntu@124.221.94.207']

remote_py = r'''
import os, sys, time, hashlib
from urllib.parse import unquote, urlparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

import requests
from utils.crawler import save_file_as_knowledge_doc
from apps.knowledge.tasks import index_document_task
from apps.knowledge.models import KnowledgeDoc

# 删除之前 failed 的 3 篇
failed = KnowledgeDoc.objects.filter(source_type='website', status='failed')
print(f'删除 {failed.count()} 篇 failed 文档')
for d in failed:
    print(f'  del id={d.id} {d.title[:30]}')
    d.delete()

# 读取缺失列表
items = []
with open('missing_pdfs.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t', 1)
        if len(parts) == 2:
            items.append((parts[0], parts[1]))

print(f'\n待下载 {len(items)} 个 PDF')
ok, fail, skip = 0, 0, 0
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

for i, (url, title) in enumerate(items, 1):
    if KnowledgeDoc.objects.filter(source_url=url[:1024]).exists():
        skip += 1
        print(f'[{i}/{len(items)}] skip: {title[:40]}')
        continue
    try:
        time.sleep(0.5)
        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        if resp.status_code != 200:
            fail += 1
            print(f'[{i}/{len(items)}] FAIL({resp.status_code}): {title[:40]}')
            continue
        raw_name = unquote(urlparse(url).path.rsplit('/', 1)[-1])
        if '.' not in raw_name:
            raw_name = title[:60] + '.pdf'
        data = {
            'content': resp.content,
            'file_name': raw_name,
            'url': url,
            'title': title,
            'md5': hashlib.md5(resp.content).hexdigest(),
            'size': len(resp.content),
        }
        doc_id = save_file_as_knowledge_doc(data, uploader_id=1)
        if doc_id:
            ok += 1
            print(f'[{i}/{len(items)}] OK id={doc_id}: {title[:40]}')
            try:
                index_document_task(doc_id)
                d = KnowledgeDoc.objects.get(id=doc_id)
                print(f'    -> status={d.status} chunks={d.chunk_count}')
            except Exception as e:
                print(f'    index warn: {e}')
        else:
            skip += 1
            print(f'[{i}/{len(items)}] dedup: {title[:40]}')
    except Exception as e:
        fail += 1
        print(f'[{i}/{len(items)}] ERROR: {title[:40]} - {type(e).__name__}: {e}')

print(f'\n完成: ok={ok} skip={skip} fail={fail}')
'''

r = subprocess.run(SSH + [
    'cd /www/wwwroot/campus_resource/backend && source venv/bin/activate && '
    'cat > download_missing.py << \'PYEOF\'\n' + remote_py + '\nPYEOF\n'
    'python -u download_missing.py 2>&1'
], capture_output=True, text=True, timeout=900, encoding='utf-8', errors='replace')
print(r.stdout)
if r.stderr and 'Traceback' in r.stderr:
    print('--- stderr ---')
    print(r.stderr[:3000])
