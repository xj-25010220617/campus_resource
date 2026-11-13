"""Test moto S3 + create Milvus bucket."""
import sys
sys.path.insert(0, '/usr/local/lib/python3.10/dist-packages')
import boto3

client = boto3.client(
    's3',
    endpoint_url='http://127.0.0.1:9000',
    aws_access_key_id='minioadmin',
    aws_secret_access_key='minioadmin',
    region_name='us-east-1',
)

print("=== moto S3 connection test ===")
resp = client.list_buckets()
print(f"Buckets: {[b['Name'] for b in resp['Buckets']]}")

BUCKET = 'a-bucket'
try:
    client.create_bucket(Bucket=BUCKET)
    print(f"Created: {BUCKET}")
except Exception as e:
    if 'BucketAlreadyOwnedByYou' in str(e):
        print(f"Already exists: {BUCKET}")
    else:
        print(f"Error: {e}")

client.put_object(Bucket=BUCKET, Key='health.txt', Body=b'moto-ready')
resp = client.get_object(Bucket=BUCKET, Key='health.txt')
print(f"Write+Read OK: {resp['Body'].read()}")

resp = client.list_buckets()
print(f"Final: {[b['Name'] for b in resp['Buckets']]}")
print("=== ALL GOOD ===")
