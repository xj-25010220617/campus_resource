"""检查 OSS 是否恢复。"""
import os, subprocess

SSH = ['ssh', '-i', r'D:\实验室204\校园虚拟资源管理\campus_server_ubuntu_ed25519',
       '-o', 'StrictHostKeyChecking=no', 'ubuntu@124.221.94.207']

remote_py = r'''
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from apps.knowledge.models import KnowledgeDoc
from utils import oss as oss_mod
import requests

doc = KnowledgeDoc.objects.filter(oss_key__isnull=False).first()
if doc:
    url = oss_mod.oss_client.get_preview_url(doc.oss_key, expires=300)
    r = requests.get(url, timeout=20)
    print('OSS GET status:', r.status_code)
    if r.status_code != 200:
        print('body:', r.text[:150].replace(chr(10), ' '))
else:
    print('no doc with oss_key')
'''

r = subprocess.run(SSH + [
    'cd /www/wwwroot/campus_resource/backend && source venv/bin/activate && '
    'cat > ck_tmp.py << \'PYEOF\'\n' + remote_py + '\nPYEOF\n'
    'python -u ck_tmp.py 2>&1; rm -f ck_tmp.py'
], capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
print(r.stdout)
if r.stderr and 'Traceback' in r.stderr:
    print(r.stderr[:1500])
