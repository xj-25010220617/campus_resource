#!/usr/bin/env bash
# 在 WSL2 中启动校园虚拟资源管理系统后端。
#
# 为什么要在 WSL 里跑：Windows 的「智能应用控制（Smart App Control）」会拦截
# grpc 原生 DLL（pymilvus / milvus-lite 依赖），导致向量库不可用。WSL 内不受该
# 策略限制，Milvus Lite 与 Embedding 链路可正常工作。
#
# 用法：
#   wsl -d trae-ubuntu2 -e bash /mnt/d/实验室204/校园虚拟资源管理/scripts/start_wsl.sh
#   wsl -d trae-ubuntu2 -e bash .../start_wsl.sh --migrate   # 启动前先迁移数据库
#
# 可覆盖的环境变量：
#   PORT                  监听端口（默认 3000）
#   MILVUS_COLLECTION     向量集合名（默认 campus_knowledge_v2）
#   EMBED_BACKEND         Embedding 后端（默认 aliyun）
#   MILVUS_URI            Milvus Lite 库路径（默认 ~/.campus_milvus.db）
set -e

PROJECT_DIR="/mnt/d/实验室204/校园虚拟资源管理/backend"
cd "$PROJECT_DIR"

# MySQL 跑在 Windows 侧：WSL 里必须用默认网关地址访问，127.0.0.1 是 WSL 自己
export DB_HOST="${DB_HOST:-$(ip route show default | awk '{print $3}')}"
export DB_PORT="${DB_PORT:-3306}"
export DJANGO_SETTINGS_MODULE=config.settings

# Windows 侧无 sentence-transformers，统一走百炼 Embedding（密钥在 .env 中）
export EMBED_BACKEND="${EMBED_BACKEND:-aliyun}"
export ALIYUN_EMBED_MODEL="${ALIYUN_EMBED_MODEL:-text-embedding-v3}"
export ALIYUN_EMBED_DIMENSIONS="${ALIYUN_EMBED_DIMENSIONS:-1024}"
export EMBED_VECTOR_DIM="${EMBED_VECTOR_DIM:-1024}"

# 向量库：默认走 Milvus Standalone（Docker，host:19530）；MILVUS_STANDALONE=0 回滚到 Lite 本地文件
export MILVUS_STANDALONE="${MILVUS_STANDALONE:-1}"
if [ -z "$MILVUS_URI" ]; then
  if [ "$MILVUS_STANDALONE" = "1" ]; then
    export MILVUS_URI="http://$DB_HOST:19530"
  else
    export MILVUS_URI="$HOME/.campus_milvus.db"
  fi
fi
export MILVUS_COLLECTION="${MILVUS_COLLECTION:-campus_knowledge_v2}"

# 分块参数（阶段 1.2：子块 350/80 进向量库，父块 1200 只存 MySQL）
# Contextual Retrieval：向量化文本前置「标题 > 标题路径」，显著提升 MRR/nDCG
export CHUNK_CHILD_SIZE="${CHUNK_CHILD_SIZE:-350}"
export CHUNK_CHILD_OVERLAP="${CHUNK_CHILD_OVERLAP:-80}"
export CHUNK_PARENT_SIZE="${CHUNK_PARENT_SIZE:-1200}"
export CONTEXTUAL_EMBED_ENABLED="${CONTEXTUAL_EMBED_ENABLED:-true}"

# Redis — Windows 侧 campus-redis 容器，WSL 用默认网关访问
# Celery broker + Django 缓存 + 分布式锁
export REDIS_HOST="${REDIS_HOST:-$DB_HOST}"
export REDIS_PORT="${REDIS_PORT:-6379}"
export REDIS_URL="${REDIS_URL:-redis://$REDIS_HOST:$REDIS_PORT/0}"
export REDIS_CACHE_URL="${REDIS_CACHE_URL:-redis://$REDIS_HOST:$REDIS_PORT/1}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://$REDIS_HOST:$REDIS_PORT/2}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-redis://$REDIS_HOST:$REDIS_PORT/3}"

# 解析：OCR 自动选择（优先 RapidOCR，CPU 更快），扫描件较厚时放宽 OCR 页数上限
export OCR_BACKEND="${OCR_BACKEND:-auto}"
export OCR_MAX_PAGES="${OCR_MAX_PAGES:-40}"

if [ "$1" = "--migrate" ]; then
  echo "[start_wsl] 应用数据库迁移..."
  python3 manage.py migrate --noinput
fi

echo "[start_wsl] DB_HOST=$DB_HOST MILVUS_COLLECTION=$MILVUS_COLLECTION PORT=${PORT:-3000}"
exec python3 manage.py runserver "0.0.0.0:${PORT:-3000}"
