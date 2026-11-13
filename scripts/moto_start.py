"""Start moto S3 mock server on 0.0.0.0:9000."""
import sys
import time
import threading

sys.path.insert(0, '/usr/local/lib/python3.10/dist-packages')

from moto.server import DomainDispatcherApplication, create_backend_app
import werkzeug.serving as ws

app = DomainDispatcherApplication(create_backend_app)
server = ws.make_server('0.0.0.0', 9000, app, threaded=True)

def run_server():
    server.serve_forever()

# 起后台线程
t = threading.Thread(target=run_server, daemon=True)
t.start()

# 等 server accept
time.sleep(0.5)
print('moto S3 mock listening on 0.0.0.0:9000', flush=True)

# 创建 Milvus bucket（此时 server 已 accept）
try:
    import boto3
    client = boto3.client(
        's3',
        endpoint_url='http://127.0.0.1:9000',
        aws_access_key_id='minioadmin',
        aws_secret_access_key='minioadmin',
        region_name='us-east-1',
    )
    try:
        client.create_bucket(Bucket='a-bucket')
        print('Created bucket: a-bucket', flush=True)
    except Exception:
        print('Bucket a-bucket already exists', flush=True)
    client.put_object(Bucket='a-bucket', Key='health.txt', Body=b'moto-milvus-ready')
    resp = client.get_object(Bucket='a-bucket', Key='health.txt')
    print(f'Write+Read OK: {resp["Body"].read()}', flush=True)
    buckets = client.list_buckets()
    print(f'All buckets: {[b["Name"] for b in buckets["Buckets"]]}', flush=True)
except Exception as e:
    print(f'Bucket setup error: {e}', flush=True)

# 主线程等
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    server.shutdown()
