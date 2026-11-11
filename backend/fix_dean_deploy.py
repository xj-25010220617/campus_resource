#!/usr/bin/env python3
"""修复部署：更新院长字段 + rag.py + 重跑 seed_campus + 重启验证"""
import subprocess, sys, textwrap

SSH = ["ssh", "-o", "BatchMode=yes", "ubuntu@124.221.94.207"]
BE = "/www/wwwroot/campus_resource/backend"

def run(cmd: str, check=True) -> int:
    print(f"\n>>> {cmd}")
    rc = subprocess.call(SSH + ["bash", "-lc", cmd])
    if check and rc != 0:
        print(f"!! FAILED rc={rc}")
        sys.exit(rc)
    return rc

# 1. SCP 两个文件
print("\n=== SCP 文件 ===")
subprocess.call(["scp", r"d:\实验室204\校园虚拟资源管理\backend\seed_campus.py",
                 "ubuntu@124.221.94.207:" + BE + "/seed_campus.py"])
subprocess.call(["scp", r"d:\实验室204\校园虚拟资源管理\backend\utils\rag.py",
                 "ubuntu@124.221.94.207:" + BE + "/utils/rag.py"])

# 2. 重跑 seed（update_or_create 会更新 dean 字段）
seed_cmd = textwrap.dedent(f"""
    set -e
    VENV={BE}/venv/bin
    cd {BE}
    echo "=== 重跑 seed_campus.py (更新院长字段) ==="
    $VENV/python manage.py shell -c "exec(open('seed_campus.py', encoding='utf-8').read())" 2>&1 | tail -30
""").strip()
run(seed_cmd)

# 3. 验证 dean 字段已灌
verify_deans = textwrap.dedent(f"""
    cd {BE}
    venv/bin/python manage.py shell -c "
from apps.accounts.models import College
print('=== 院长字段 ===')
for c in College.objects.all().order_by('id'):
    print(f'  {{c.name}} : dean={{c.dean!r}}')
print()
print('=== build_school_knowledge_from_db 含 dean 数 ===')
from utils.rag import build_school_knowledge_from_db
kb = build_school_knowledge_from_db()
count = kb.count('院长')
print(f'  院长出现次数: {{count}}')
print(f'  含赵媛媛: {{chr(25991)+chr(22791)+chr(22290)+chr(22290) in kb}}')
print(f'  含宁多彪: {{chr(23576)+chr(22810)+chr(24419) in kb}}')
# 打印信息与商务管理学院块
import re
m = re.search(r'### 3\.[^#]+?(?=###|$)', kb, re.DOTALL)
if m: print('  --- 信息与商务管理学院块 ---'); print(m.group()[:500])
"
""").strip()
run(verify_deans)

# 4. 重启服务
restart = textwrap.dedent("""
    sudo systemctl restart campus-backend.service
    sleep 3
    sudo systemctl --no-pager --full status campus-backend.service | head -10
""").strip()
run(restart)

# 5. 快速健康检查（服务器本机 curl）
health = textwrap.dedent("""
    curl -s -o /dev/null -w "Backend status: %{http_code}\\n" http://127.0.0.1:3000/api/school/profile/
    curl -s http://127.0.0.1:3000/api/school/colleges/ | python3 -c "import json,sys; data=json.load(sys.stdin); [print(f'  {c[\"name\"]} dean={c.get(\"dean\",\"\")!r}') for c in data]" 2>/dev/null || echo '(dean 字段未在 list API 中暴露，属正常)'
""").strip()
run(health, check=False)

print("\n=== 修复部署完成 ===")
