#!/usr/bin/env bash
# 快速验证 WSL 里能否连上 Milvus Standalone
set +e
echo "=== Check Milvus Standalone connectivity ==="

# WSL 访问 Docker 端口用 default gateway
export DB_HOST="$(ip route show default | awk '{print $3}')"

echo "DB_HOST=$DB_HOST"
echo "MILVUS_URI=http://$DB_HOST:19530"

python3 << PYEOF
import sys
sys.path.insert(0, '/mnt/d/实验室204/校园虚拟资源管理/backend')

from pymilvus import connections, utility

URI = 'http://${DB_HOST}:19530'
print(f'Connecting to: {URI}')

try:
    connections.connect(
        alias='standalone',
        uri=URI,
        timeout=10
    )
    print('CONNECTED ✅')
    
    # 列出所有集合
    collections = utility.list_collections(using='standalone')
    print(f'Collections: {collections}')
    
    # 检查 campus_knowledge_v2 是否存在
    if 'campus_knowledge_v2' in collections:
        print('campus_knowledge_v2 EXISTS')
        stats = utility.get_collection_stats('campus_knowledge_v2', using='standalone')
        print(f'Stats: {stats}')
    else:
        print('campus_knowledge_v2 NOT EXISTS — need reindex')
    
except Exception as e:
    print(f'CONNECT FAILED: {e}')
    import traceback; traceback.print_exc()

connections.disconnect('standalone')
print('=== DONE ===')
PYEOF
