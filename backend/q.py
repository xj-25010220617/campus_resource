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
qs = KnowledgeDoc.objects.filter(source_type='website')
print('total:', qs.count(), '| indexed:', qs.filter(status='indexed').count(), '| pdf:', qs.filter(file_type='pdf').count())
non = qs.exclude(status='indexed').order_by('id')
print('non-indexed:', non.count())
for d in non:
    print(f'  id={d.id} status={d.status} ft={d.file_type} chunks={d.chunk_count} {d.title[:30]}')
'''
r = subprocess.run(SSH + [
    'cd /www/wwwroot/campus_resource/backend && source venv/bin/activate && '
    'cat > q_tmp.py << \'PYEOF\'\n' + remote_py + '\nPYEOF\n'
    'python -u q_tmp.py 2>&1; rm -f q_tmp.py'
], capture_output=True, text=True, timeout=180, encoding='utf-8', errors='replace')
print(r.stdout)
