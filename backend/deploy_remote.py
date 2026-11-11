#!/usr/bin/env python3
"""远程部署后端脚本 —— 在本地 Windows 运行，通过 SSH 推送到 /www/wwwroot/campus_resource/backend"""
import subprocess, sys, textwrap, shlex

SSH = ["ssh", "-o", "BatchMode=yes", "ubuntu@124.221.94.207"]
BE = "/www/wwwroot/campus_resource/backend"
TS_CMD = "date +%Y%m%d%H%M%S"

def run(cmd: str, check=True) -> int:
    print(f"\n>>> {cmd}")
    rc = subprocess.call(SSH + ["bash", "-lc", cmd])
    if check and rc != 0:
        print(f"!! FAILED rc={rc}")
        sys.exit(rc)
    return rc

# 1. 备份 + 拷贝
backup_copy = textwrap.dedent(f"""
    set -e
    B={BE}
    TS=$({TS_CMD})
    echo "=== 备份标记: $TS ==="
    
    # 备份现有文件
    for p in \\
      apps/accounts/admin.py \\
      apps/accounts/models.py \\
      apps/accounts/school_views.py \\
      apps/accounts/school_urls.py \\
      apps/accounts/migrations/0009_academiccalendar_admissionrule_scholarship_and_more.py \\
      apps/classify/admin.py \\
      apps/classify/models.py \\
      apps/classify/migrations/0005_alter_discipline_options_and_more.py \\
      config/urls.py \\
      utils/rag.py \\
      seed_campus.py ; do
      if [ -f "$B/$p" ]; then
        cp "$B/$p" "$B/$p.bak.$TS"
        echo "  备份: $B/$p"
      fi
    done
    
    # 拷贝新文件
    cp /tmp/bk-accounts-admin.py    "$B/apps/accounts/admin.py"
    cp /tmp/bk-accounts-models.py   "$B/apps/accounts/models.py"
    cp /tmp/bk-school-views.py     "$B/apps/accounts/school_views.py"
    cp /tmp/bk-school-urls.py      "$B/apps/accounts/school_urls.py"
    cp /tmp/bk-mig-accounts.py     "$B/apps/accounts/migrations/0009_academiccalendar_admissionrule_scholarship_and_more.py"
    
    cp /tmp/bk-classify-admin.py    "$B/apps/classify/admin.py"
    cp /tmp/bk-classify-models.py   "$B/apps/classify/models.py"
    cp /tmp/bk-mig-classify.py     "$B/apps/classify/migrations/0005_alter_discipline_options_and_more.py"
    
    cp /tmp/bk-config-urls.py       "$B/config/urls.py"
    cp /tmp/bk-rag.py               "$B/utils/rag.py"
    cp /tmp/bk-seed-campus.py       "$B/seed_campus.py"
    
    echo "=== 文件分发完成 ==="
    ls -la "$B/apps/accounts/migrations/" "$B/apps/classify/migrations/" | tail -10
""").strip()

run(backup_copy)

# 2. Django check + migrate + seed
migrate_seed = textwrap.dedent(f"""
    set -e
    VENV={BE}/venv/bin
    cd {BE}
    echo "=== Django check ==="
    $VENV/python manage.py check --deploy 2>&1 | tail -5
    echo ""
    echo "=== Migrations ==="
    $VENV/python manage.py migrate --noinput 2>&1 | tail -15
    echo ""
    echo "=== Seed campus data ==="
    $VENV/python manage.py shell -c "exec(open('seed_campus.py', encoding='utf-8').read())" 2>&1 | tail -25
""").strip()

run(migrate_seed)

# 3. Restart services
restart = textwrap.dedent(f"""
    echo "=== Restart campus-backend ==="
    sudo systemctl restart campus-backend.service
    sleep 2
    sudo systemctl --no-pager --full status campus-backend.service | head -15
    echo ""
    echo "=== Restart campus-celery ==="
    sudo systemctl restart campus-celery.service
    sleep 2
    sudo systemctl --no-pager --full status campus-celery.service | head -10
""").strip()

run(restart)

# 4. Health check
health = textwrap.dedent("""
    echo "=== Health check ==="
    curl -s -o /dev/null -w "Backend: %{http_code}\\n" http://127.0.0.1:3000/api/school/profile/ || echo "school_profile 404 expected if DB not ready"
    curl -s -o /dev/null -w "Auth API: %{http_code}\\n" http://127.0.0.1:3000/api/auth/login/
    echo ""
    echo "=== school/profile response ==="
    curl -s http://127.0.0.1:3000/api/school/profile/ | head -c 400
    echo ""
""").strip()

run(health, check=False)

print("\n=== 部署完成 ===")
