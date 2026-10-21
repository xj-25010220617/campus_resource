"""检查最新文档状态。"""
import subprocess

SSH = ['ssh', '-i', r'D:\实验室204\校园虚拟资源管理\campus_server_ubuntu_ed25519',
       '-o', 'StrictHostKeyChecking=no', 'ubuntu@124.221.94.207']

remote_py = r'''
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from apps.knowledge.models import KnowledgeDoc
from django.db.models import Count
qs = KnowledgeDoc.objects.filter(source_type='website')
print('website total:', qs.count())
print('by status:', list(qs.values('status').annotate(c=Count('id'))))
print('by file_type:', list(qs.values('file_type').annotate(c=Count('id'))))
pending = qs.exclude(status='indexed')
print('pending:', pending.count())
for d in pending.order_by('id')[:10]:
    print(f'  {d.id} {d.status} {d.file_type} {d.title[:35]}')
'''
r = subprocess.run(SSH + [
    'cd /www/wwwroot/campus_resource/backend && source venv/bin/activate && '
    'cat > ck_tmp.py << \'PYEOF\'\n' + remote_py + '\nPYEOF\n'
    'python -u ck_tmp.py 2>&1; rm -f ck_tmp.py'
], capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
print(r.stdout)
