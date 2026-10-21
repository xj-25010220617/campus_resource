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
from apps.knowledge.tasks import index_document_task

pending = KnowledgeDoc.objects.filter(source_type='website', file_type='pdf').exclude(status='indexed').order_by('id')
print(f'待索引 {pending.count()} 篇')
for d in pending:
    d.status = 'uploaded'
    d.save()
    try:
        index_document_task(d.id)
        d.refresh_from_db()
        print(f'  id={d.id} -> {d.status} chunks={d.chunk_count}')
    except Exception as e:
        print(f'  id={d.id} error: {type(e).__name__}: {e}')
'''
r = subprocess.run(SSH + [
    'cd /www/wwwroot/campus_resource/backend && source venv/bin/activate && '
    'cat > fix_tmp.py << \'PYEOF\'\n' + remote_py + '\nPYEOF\n'
    'python -u fix_tmp.py 2>&1; rm -f fix_tmp.py'
], capture_output=True, text=True, timeout=600, encoding='utf-8', errors='replace')
print(r.stdout)
