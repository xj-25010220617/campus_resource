#!/usr/bin/env bash
# 全量重灌到 Milvus Standalone（阿里云百炼 Embedding）
set -e
cd /mnt/d/实验室204/校园虚拟资源管理/backend

export DB_HOST="$(ip route show default | awk '{print $3}')"
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings

# Milvus Standalone
export MILVUS_URI="http://$DB_HOST:19530"
export MILVUS_COLLECTION="campus_knowledge_v2"

# Redis
export REDIS_HOST="$DB_HOST"
export REDIS_PORT=6379
export REDIS_CACHE_URL="redis://$REDIS_HOST:$REDIS_PORT/1"
export CELERY_BROKER_URL="redis://$REDIS_HOST:$REDIS_PORT/2"
export CELERY_RESULT_BACKEND="redis://$REDIS_HOST:$REDIS_PORT/3"

# Embedding: 阿里云百炼（专有实例 endpoint）
export EMBED_BACKEND=aliyun
export ALIYUN_EMBED_BASE_URL="https://ws-xrd31dg17vp9izmi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
# ALIYUN_EMBED_API_KEY 与 QWEN_API_KEY 从 backend/.env 或环境变量读取，禁止硬编码。
export ALIYUN_EMBED_MODEL="text-embedding-v3"
export EMBED_VECTOR_DIM=1024

# Django Chat 兼容
export QWEN_BASE_URL="https://api.deepseek.com"
export QWEN_CHAT_MODEL="deepseek-flash"

echo "========================================"
echo "  REINDEX ALL → MILVUS STANDALONE"
echo "========================================"
echo "  DB_HOST=$DB_HOST"
echo "  MILVUS_URI=$MILVUS_URI"
echo "  EMBED_BACKEND=$EMBED_BACKEND ($ALIYUN_EMBED_MODEL, dim=$EMBED_VECTOR_DIM)"
echo "  Started at: $(date '+%H:%M:%S')"
echo "========================================"

python3 scripts/reindex_all.py 2>&1

echo ""
echo "  Finished at: $(date '+%H:%M:%S')"
echo "========================================"
