"""Test DeepSeek Embedding API"""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()

from utils.http_client import ExternalClient
from django.conf import settings

api_key = settings.DEEPSEEK_API_KEY
base_url = getattr(settings, 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
model = getattr(settings, 'DEEPSEEK_EMBED_MODEL', 'deepseek-embed')

print(f'Using API Key: {api_key[:8]}...{api_key[-4:]}')
print(f'Base URL: {base_url}')
print(f'Model: {model}')
print()

# Test embedding endpoint
resp = ExternalClient.post(
    'deepseek_embed_test',
    f'{base_url}/v1/embeddings',
    headers={
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    },
    json={
        'model': model,
        'input': ['Hello world'],
    },
    timeout=15,
)
print(f'Status: {resp.status_code}')
print(f'Body: {resp.text[:500]}')
