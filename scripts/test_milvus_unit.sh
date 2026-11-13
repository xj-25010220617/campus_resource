#!/usr/bin/env bash
set +e
cd /mnt/d/实验室204/校园虚拟资源管理/backend
export DB_HOST="$(ip route show default | awk '{print $3}')"
export DB_PORT=3306
export DJANGO_SETTINGS_MODULE=config.settings
export EMBED_BACKEND=mock
unset MILVUS_URI
unset REDIS_CACHE_URL
export SKIP_STARTUP_HEALTH_CHECK=true

python3 manage.py test apps.knowledge.tests.test_milvus_client apps.knowledge.tests.test_embedding --verbosity=1 2>&1 | tail -30
