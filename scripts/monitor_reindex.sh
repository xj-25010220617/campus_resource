#!/usr/bin/env bash
# 每分钟查一次，直到 mark 消失或 entities 稳定
set +e
cd /mnt/d/实验室204/校园虚拟资源管理

DB_HOST="$(ip route show default | awk '{print $3}')"
MILVUS_URI="http://$DB_HOST:19530"
export DB_HOST MILVUS_URI

LAST=-1
STABLE_COUNT=0
START=$(date +%s)

for i in $(seq 1 90); do
    # 查 entities
    CUR=$(python3 -c "
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ['DB_HOST'] = '$DB_HOST'
os.environ['MILVUS_URI'] = '$MILVUS_URI'
import django; django.setup()
from pymilvus import connections, Collection, utility
connections.connect(uri='$MILVUS_URI')
if utility.has_collection('campus_knowledge_v2'):
    c = Collection('campus_knowledge_v2')
    print(c.num_entities)
else:
    print('0')
" 2>/dev/null)

    NOW=$(date '+%H:%M:%S')
    
    # mark 消失 = 重灌完成
    if [ ! -f /tmp/campus_reindex_incomplete ] && [ -f /mnt/d/实验室204/校园虚拟资源管理/backend/scripts/reindex_all.py ]; then
        ELAPSED=$((($(date +%s) - START) / 60))
        echo "[$NOW] ✅ REINDEX DONE! entities=$CUR, elapsed=${ELAPSED}m"
        exit 0
    fi
    
    if [ "$CUR" = "$LAST" ]; then
        STABLE_COUNT=$((STABLE_COUNT + 1))
        if [ $STABLE_COUNT -ge 3 ]; then
            echo "[$NOW] entities=$CUR (stable 3x — checking if done...)"
        fi
    else
        STABLE_COUNT=0
        DIFF=$((CUR - LAST))
        [ $LAST -ge 0 ] && echo "[$NOW] entities=$CUR (+$DIFF)" || echo "[$NOW] entities=$CUR (initial)"
        LAST=$CUR
    fi
    
    sleep 60
done

echo "TIMEOUT after 90 iterations"
