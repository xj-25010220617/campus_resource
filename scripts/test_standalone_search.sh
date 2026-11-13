#!/usr/bin/env bash
# 验证 Milvus Standalone 真实向量检索（先 embed 再 search）
set +e
cd /mnt/d/实验室204/校园虚拟资源管理/backend

export DB_HOST="$(ip route show default | awk '{print $3}')"
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings
export MILVUS_URI="http://$DB_HOST:19530"
export MILVUS_COLLECTION="campus_knowledge_v2"
export EMBED_BACKEND=aliyun
export ALIYUN_EMBED_BASE_URL="https://ws-xrd31dg17vp9izmi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
# ALIYUN_EMBED_API_KEY 从 backend/.env 或环境变量读取，禁止硬编码。
export ALIYUN_EMBED_MODEL="text-embedding-v3"
export EMBED_VECTOR_DIM=1024

python3 << 'PYEOF'
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django; django.setup()

from utils.milvus import milvus_client
from utils.llm import llm_client

print(f"MILVUS_URI = {os.environ['MILVUS_URI']}")
assert milvus_client.connect(), 'connect failed'
print("connected: True")

for q in ['图书馆开放时间', '教务系统登录入口', '奖学金评定标准']:
    vec = llm_client.embed([q], is_query=True)[0]
    hits = milvus_client.search(vec, top_k=3)
    print(f"\n--- query: {q} (embed dim={len(vec)}) ---")
    if not hits:
        print("  NO HITS")
        continue
    for i, h in enumerate(hits, 1):
        text = (h.get('chunk_text') or '')[:60].replace('\n', ' ')
        print(f"  [{i}] score={h['score']:.4f} doc_id={h['doc_id']} text={text}...")
PYEOF
echo "=== DONE ==="
