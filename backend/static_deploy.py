#!/usr/bin/env python3
"""静态字段补全 — 服务器部署脚本"""
import subprocess, sys, textwrap, time

SSH = ["ssh", "-o", "BatchMode=yes", "ubuntu@124.221.94.207"]
BE = "/www/wwwroot/campus_resource/backend"

def run(cmd: str, check=True) -> int:
    print(f"\n>>> {cmd[:100]}")
    rc = subprocess.call(SSH + ["bash", "-lc", cmd])
    if check and rc != 0:
        print(f"!! FAILED rc={rc}")
        sys.exit(rc)
    return rc

print("=== 1. SCP 4 个文件 ===")
files = [
    (r"d:\实验室204\校园虚拟资源管理\backend\apps\accounts\models.py",
     BE + "/apps/accounts/models.py"),
    (r"d:\实验室204\校园虚拟资源管理\backend\apps\accounts\migrations\0010_college_secretary_schoolprofile_leaders_text.py",
     BE + "/apps/accounts/migrations/0010_college_secretary_schoolprofile_leaders_text.py"),
    (r"d:\实验室204\校园虚拟资源管理\backend\utils\rag.py",
     BE + "/utils/rag.py"),
    (r"d:\实验室204\校园虚拟资源管理\backend\seed_campus.py",
     BE + "/seed_campus.py"),
]
for local, remote in files:
    subprocess.call(["scp", local, "ubuntu@124.221.94.207:" + remote])

migrate_seed = textwrap.dedent(f"""
    set -e
    VENV={BE}/venv/bin
    cd {BE}
    echo "=== Backup ==="
    for f in apps/accounts/models.py utils/rag.py apps/accounts/migrations/0010_*.py seed_campus.py; do
      [ -f "$f" ] && cp "$f" "$f.bak.$(date +%H%M%S)"
    done
    echo "=== migrate ==="
    $VENV/python manage.py migrate --noinput 2>&1 | tail -8
    echo ""
    echo "=== seed_campus ==="
    $VENV/python manage.py shell -c "exec(open('seed_campus.py', encoding='utf-8').read())" 2>&1 | tail -15
""").strip()
run(migrate_seed)

verify = textwrap.dedent(f"""
    cd {BE}
    venv/bin/python manage.py shell -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from apps.accounts.models import College, SchoolProfile
sp = SchoolProfile.objects.first()
print('=== 校领导 ===')
print(f'  党委书记: {{sp.party_secretary!r}}')
print(f'  校长: {{sp.president!r}}')
print(f'  leaders_text 字符数: {{len(sp.leaders_text or '')}}')
print()
print('=== 学院院长/书记 ===')
for c in College.objects.filter(secretary__gt='').order_by('id'):
    print(f'  {{c.name}} : dean={{c.dean or \"?\"}} secretary={{c.secretary!r}}')
print()
from utils.rag import build_school_knowledge_from_db
kb = build_school_knowledge_from_db()
print(f'RAG 总字符: {{len(kb)}}')
print(f'含赖廷谦: {{\"赖廷谦\" in kb}}')
print(f'含沈洪科: {{\"沈洪科\" in kb}}')
print(f'含罗玺: {{\"罗玺\" in kb}}')
print(f'含赵壁: {{\"赵壁\" in kb}}')
print(f'含校领导班子: {{\"校领导班子\" in kb}}')
" 2>&1 | tail -40
""").strip()
run(verify, check=False)

restart = textwrap.dedent("""
    sudo systemctl restart campus-backend.service
    sleep 3
    sudo systemctl --no-pager --full status campus-backend.service | head -8
""").strip()
run(restart)

health = textwrap.dedent("""
    curl -s -o /dev/null -w "API: %{http_code}\\n" http://127.0.0.1:3000/api/school/profile/
""").strip()
run(health, check=False)

print("\n=== 静态字段部署完成 ===")
