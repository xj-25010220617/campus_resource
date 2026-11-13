#!/usr/bin/env bash
cd /mnt/d/实验室204/校园虚拟资源管理/backend
export DB_HOST="$(ip route show default | awk '{print $3}')"
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings
export EMBED_BACKEND="${EMBED_BACKEND:-aliyun}"
export MILVUS_URI="http://$DB_HOST:19530"
export MILVUS_COLLECTION="campus_knowledge_v2"

echo "=== Embedding Config Check ==="
python3 << PYEOF
import os
from decouple import config
print(f"EMBED_BACKEND env: {os.environ.get('EMBED_BACKEND', '(not set)')}")
print(f"ALIYUN_EMBED_API_KEY: {('SET' if config('ALIYUN_EMBED_API_KEY', default='') else 'EMPTY')}")
print(f"QWEN_API_KEY: {('SET' if config('QWEN_API_KEY', default='') else 'EMPTY')}")
PYEOF

echo ""
echo "=== Test Real Embedding ==="
python3 << PYEOF
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from utils.embedding import get_embedding

vec = get_embedding('图书馆开放时间')
print(f'Embedding OK: dim={len(vec)}, sample={vec[:3]}')
PYEOF

echo ""
echo "=== Milvus Standalone Check ==="
python3 << PYEOF
import os, sys
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from utils.milvus import milvus_client
milvus_client.connect()
print(f'Connected: {milvus_client.connected}')
print(f'Collection: {milvus_client.COLLECTION_NAME}')
print(f'Vector dim: {milvus_client.VECTOR_DIM}')
PYEOF
echo "=== DONE ==="
