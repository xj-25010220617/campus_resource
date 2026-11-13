#!/bin/bash
# 验证 Redis 连通 + Django check（带 Redis 缓存）
set -e
cd /mnt/d/实验室204/校园虚拟资源管理/backend

export DB_HOST="$(ip route show default | awk '{print $3}')"
export REDIS_HOST="$DB_HOST"
export REDIS_PORT=6379
export REDIS_CACHE_URL="redis://$REDIS_HOST:$REDIS_PORT/1"
export CELERY_BROKER_URL="redis://$REDIS_HOST:$REDIS_PORT/2"
export CELERY_RESULT_BACKEND="redis://$REDIS_HOST:$REDIS_PORT/3"
export EMBED_BACKEND=aliyun
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings

echo "=== DB_HOST=$DB_HOST REDIS=$REDIS_HOST:$REDIS_PORT ==="

echo "=== Redis ping ==="
python3 -c "import redis; r=redis.Redis(host='$REDIS_HOST',port=6379,db=0); print('PONG' if r.ping() else 'FAIL')" 2>&1

echo "=== Django check ==="
python3 manage.py check 2>&1

echo "=== Cache backend check ==="
python3 -c "
import django; django.setup()
from django.core.cache import cache
cache.set('test', 'hello', 30)
v = cache.get('test')
print(f'Cache backend: {type(cache).__name__} -> get={v}')
" 2>&1

echo "=== Celery broker check ==="
python3 -c "
import django; django.setup()
from django.conf import settings
print(f'BROKER: {settings.CELERY_BROKER_URL}')
print(f'BACKEND: {settings.CELERY_RESULT_BACKEND}')
from celery import Celery
app = Celery('test', broker=settings.CELERY_BROKER_URL, backend=settings.CELERY_RESULT_BACKEND)
conn = app.connection()
print(f'Celery connectable: {conn.connect()}')
conn.release()
" 2>&1

echo "=== ALL GOOD ==="
