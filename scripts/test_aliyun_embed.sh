#!/usr/bin/env bash
set +e
cd /mnt/d/实验室204/校园虚拟资源管理/backend

export DB_HOST="$(ip route show default | awk '{print $3}')"
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings

export EMBED_BACKEND=aliyun
export ALIYUN_EMBED_BASE_URL="https://ws-xrd31dg17vp9izmi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
# ALIYUN_EMBED_API_KEY 从 backend/.env 或环境变量读取，禁止硬编码。
export ALIYUN_EMBED_MODEL="text-embedding-v3"

echo "=== Test Real Embedding ==="
python3 << 'PYEOF'
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from utils.llm import llm_client
try:
    vec = llm_client.embed(['图书馆开放时间', '教务系统登录入口'])
    print(f'Embedding OK: len={len(vec)} items, dim={len(vec[0])}')
    print(f'Sample vec[0][:5]: {vec[0][:5]}')
except Exception as e:
    print(f'FAILED: {e}')
    import traceback; traceback.print_exc()
PYEOF
echo "=== DONE ==="
