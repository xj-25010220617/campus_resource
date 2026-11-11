#!/usr/bin/env python3
"""在服务器上手动触发一次官网爬虫（20 页快速验证）"""
import subprocess, sys, textwrap

SSH = ["ssh", "-o", "BatchMode=yes", "ubuntu@124.221.94.207"]

def run(cmd: str) -> int:
    print(f"\n>>> {cmd[:120]}")
    return subprocess.call(SSH + ["bash", "-lc", cmd])

# 1. 写入 beat service + 启动
beat_setup = textwrap.dedent(r"""
set -e
BE=/www/wwwroot/campus_resource/backend

# 检查 django_celery_beat 是否已安装（systemd beat 用这个 scheduler）
$BE/venv/bin/pip list 2>/dev/null | grep -i celerybeat || $BE/venv/bin/pip install django-celery-beat -q

# 写 service 文件
sudo tee /etc/systemd/system/campus-celery-beat.service > /dev/null << 'EOF'
[Unit]
Description=Campus Resource Celery Beat (定时调度爬虫等任务)
After=network.target campus-celery.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/www/wwwroot/campus_resource/backend
Environment=DJANGO_SETTINGS_MODULE=config.settings
ExecStart=/www/wwwroot/campus_resource/backend/venv/bin/celery -A config beat --loglevel=info --logfile=/home/ubuntu/apps/logs/celery-beat.log
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable campus-celery-beat.service
sudo systemctl restart campus-celery-beat.service
sleep 2
sudo systemctl --no-pager --full status campus-celery-beat.service | head -10
""").strip()
run(beat_setup)

# 2. 手动触发一次爬虫（20 页快速验证）
crawl_now = textwrap.dedent(r"""
set -e
BE=/www/wwwroot/campus_resource/backend
cd $BE
echo "=== 手动触发爬虫 (max_pages=30) ==="
timeout 180 venv/bin/python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import sys; sys.path.insert(0, '$BE')
import django; django.setup()
from apps.knowledge.tasks import crawl_neusoft_website_task
saved = crawl_neusoft_website_task(max_pages=30, uploader_id=1, download_files=False)
print(f'爬取完成，保存 {saved} 篇文档')
" 2>&1 | tail -25
""").strip()
run(crawl_now)

# 3. 等索引完成后检查
verify = textwrap.dedent(r"""
BE=/www/wwwroot/campus_resource/backend
cd $BE
echo "=== 等待索引 (30s) ==="
sleep 30
venv/bin/python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import sys; sys.path.insert(0, '$BE')
import django; django.setup()
from apps.knowledge.models import KnowledgeDoc, DocChunk
total = KnowledgeDoc.objects.count()
website = KnowledgeDoc.objects.filter(source_type='website').count()
indexed_web = KnowledgeDoc.objects.filter(source_type='website', status='indexed').count()
chunks = DocChunk.objects.count()
print(f'总文档={total} website={website} website_indexed={indexed_web} 切片={chunks}')
for d in KnowledgeDoc.objects.filter(source_type='website').order_by('-created_at')[:5]:
    print(f'  [{d.status}] {d.title[:60]}')
" 2>&1
""").strip()
run(verify)

print("\n=== 完成 ===")
