#!/usr/bin/env bash
# 快速验证：Celery worker 能否连上 Redis broker 并正常启动
set +e

PROJECT_DIR="/mnt/d/实验室204/校园虚拟资源管理/backend"
cd "$PROJECT_DIR"

export DB_HOST="${DB_HOST:-$(ip route show default | awk '{print $3}')}"
export DB_PORT="${DB_PORT:-3306}"
export DJANGO_SETTINGS_MODULE=config.settings
export REDIS_HOST="$DB_HOST"
export REDIS_PORT=6379
export REDIS_CACHE_URL="redis://$REDIS_HOST:$REDIS_PORT/1"
export CELERY_BROKER_URL="redis://$REDIS_HOST:$REDIS_PORT/2"
export CELERY_RESULT_BACKEND="redis://$REDIS_HOST:$REDIS_PORT/3"
export EMBED_BACKEND=aliyun

echo "=== CELERY WORKER QUICK CHECK ==="
echo "BROKER=$CELERY_BROKER_URL"
echo ""

# 1. 检查 celery 是否已安装
echo "--- celery version ---"
python3 -c "import celery; print(celery.__version__)" 2>&1 || { echo "celery NOT INSTALLED"; exit 1; }

# 2. 检查 kombu Queue 能正常 import（settings 里 CELERY_QUEUES 声明依赖）
echo "--- kombu.Queue ---"
python3 -c "from kombu import Queue; print('OK:', Queue)" 2>&1

# 3. 检查 worker 能否启动（--loglevel=INFO --queues=parse,default 绑定两队列，启动 3 秒后 kill）
echo "--- worker start test (3s timeout) ---"
timeout 15 python3 -m celery -A config worker \
  --loglevel=INFO \
  --concurrency=2 \
  --queues=parse,default 2>&1 &
WPID=$!
sleep 10
if kill -0 $WPID 2>/dev/null; then
  echo "WORKER IS RUNNING (PID=$WPID) — SUCCESS"
  kill $WPID 2>/dev/null
else
  echo "WORKER FAILED TO STAY ALIVE — check logs above"
fi
wait $WPID 2>/dev/null
echo "=== DONE ==="
