#!/usr/bin/env bash
# 带完整 env 的 Django 启动（--noreload 稳定模式）
set -e

PROJECT_DIR="/mnt/d/实验室204/校园虚拟资源管理/backend"
cd "$PROJECT_DIR"

# MySQL 跑在 Windows 侧：WSL 里必须用默认网关地址访问
export DB_HOST="${DB_HOST:-$(ip route show default | awk '{print $3}')}"
export DB_PORT="${DB_PORT:-3306}"
export DJANGO_SETTINGS_MODULE=config.settings

# Embedding
export EMBED_BACKEND="${EMBED_BACKEND:-aliyun}"
export ALIYUN_EMBED_MODEL="${ALIYUN_EMBED_MODEL:-text-embedding-v3}"
export ALIYUN_EMBED_DIMENSIONS="${ALIYUN_EMBED_DIMENSIONS:-1024}"
export EMBED_VECTOR_DIM="${EMBED_VECTOR_DIM:-1024}"

# Milvus：默认走 Standalone（Docker host:19530）；MILVUS_STANDALONE=0 回滚到 Lite 本地文件
export MILVUS_STANDALONE="${MILVUS_STANDALONE:-1}"
if [ -z "$MILVUS_URI" ]; then
  if [ "$MILVUS_STANDALONE" = "1" ]; then
    export MILVUS_URI="http://$DB_HOST:19530"
  else
    export MILVUS_URI="$HOME/.campus_milvus.db"
  fi
fi
export MILVUS_COLLECTION="${MILVUS_COLLECTION:-campus_knowledge_v2}"

# 分块参数
export CHUNK_CHILD_SIZE="${CHUNK_CHILD_SIZE:-350}"
export CHUNK_CHILD_OVERLAP="${CHUNK_CHILD_OVERLAP:-80}"
export CHUNK_PARENT_SIZE="${CHUNK_PARENT_SIZE:-1200}"
export CONTEXTUAL_EMBED_ENABLED="${CONTEXTUAL_EMBED_ENABLED:-true}"

# Redis — Windows 侧 campus-redis 容器
export REDIS_HOST="${REDIS_HOST:-$DB_HOST}"
export REDIS_PORT="${REDIS_PORT:-6379}"
export REDIS_URL="${REDIS_URL:-redis://$REDIS_HOST:$REDIS_PORT/0}"
export REDIS_CACHE_URL="${REDIS_CACHE_URL:-redis://$REDIS_HOST:$REDIS_PORT/1}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://$REDIS_HOST:$REDIS_PORT/2}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-redis://$REDIS_HOST:$REDIS_PORT/3}"

# OCR
export OCR_BACKEND="${OCR_BACKEND:-auto}"
export OCR_MAX_PAGES="${OCR_MAX_PAGES:-40}"

echo "[start_server] DB_HOST=$DB_HOST"
echo "[start_server] MILVUS_URI=$MILVUS_URI"
echo "[start_server] MILVUS_COLLECTION=$MILVUS_COLLECTION"
echo "[start_server] REDIS_CACHE_URL=$REDIS_CACHE_URL"
echo "[start_server] CELERY_BROKER_URL=$CELERY_BROKER_URL"
echo "[start_server] PORT=${PORT:-3000}"
echo "[start_server] starting runserver --noreload ..."

exec python3 manage.py runserver "0.0.0.0:${PORT:-3000}" --noreload
