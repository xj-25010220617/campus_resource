#!/usr/bin/env bash
set +e

# 先清掉旧进程
pkill -f "moto" 2>/dev/null || true
sleep 1

echo "=== Check moto_server cmd ==="
which moto_server 2>&1
moto_server --help 2>&1 | head -5 || echo "no cmd, will use python"

echo ""
echo "=== Start moto server ==="
# 用 Python 直接启动（修复：ThreadedMotoServer 没有 host 参数，
# 需要直接用 moto.server.DomainDispatcherApplication + WSGIServer 绑定 0.0.0.0）
python3 << 'PYEOF' &
import sys
sys.path.insert(0, '/usr/local/lib/python3.10/dist-packages')

from moto.server import DomainDispatcherApplication, create_backend_app
from werkzeug.serving import make_server

app = DomainDispatcherApplication(create_backend_app)
server = make_server('0.0.0.0', 9000, app, threaded=True)
print(f"[moto] S3 mock listening on 0.0.0.0:9000", flush=True)
server.serve_forever()
PYEOF
MOTO_PID=$!
echo "[moto] PID=$MOTO_PID"

sleep 3
echo ""
echo "=== Test S3 + create bucket ==="
python3 << 'PYEOF'
import boto3

client = boto3.client(
    's3',
    endpoint_url='http://127.0.0.1:9000',
    aws_access_key_id='minioadmin',
    aws_secret_access_key='minioadmin',
    region_name='us-east-1',
)

print("List existing buckets...")
try:
    resp = client.list_buckets()
    print(f"  Existing: {[b['Name'] for b in resp['Buckets']]}")
except Exception as e:
    print(f"  List error: {e}")

BUCKET = 'a-bucket'
try:
    client.create_bucket(Bucket=BUCKET)
    print(f"  Created: {BUCKET}")
except Exception as e:
    if 'BucketAlreadyOwnedByYou' in str(e):
        print(f"  Already exists: {BUCKET}")
    else:
        print(f"  Create error: {e}")

# 写入测试
try:
    client.put_object(Bucket=BUCKET, Key='test.txt', Body=b'moto-milvus-test')
    resp = client.get_object(Bucket=BUCKET, Key='test.txt')
    print(f"  Write+Read OK: {resp['Body'].read()}")
except Exception as e:
    print(f"  R/W error: {e}")

resp = client.list_buckets()
print(f"  Final buckets: {[b['Name'] for b in resp['Buckets']]}")
PYEOF

echo ""
echo "=== MOTOR S3 MOCK READY ==="
echo "  Endpoint: http://host.docker.internal:9000"
echo "  Bucket: a-bucket"
echo "  Access/Secret: minioadmin / minioadmin"
echo "  PID=$MOTO_PID"
echo "  Press Ctrl+C to stop"

wait $MOTO_PID 2>/dev/null
