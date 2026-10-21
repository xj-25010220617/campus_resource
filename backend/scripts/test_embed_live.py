"""隔离测试：Embedding 通道是否正常（不碰 Milvus）"""
import os
import sys
import time

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.conf import settings

print('EMBED_BACKEND =', getattr(settings, 'EMBED_BACKEND', '?'))
print('EMBED_MODEL   =', getattr(settings, 'EMBED_MODEL', '?'))
print('EMBED_VECTOR_DIM =', getattr(settings, 'EMBED_VECTOR_DIM', '?'))

from utils.embed import embed_query

t0 = time.time()
try:
    vec = embed_query('15级优秀毕业生有谁')
    print('embed 成功: 维度=', len(vec), ' 首值=', vec[:3], f' 耗时={time.time()-t0:.2f}s')
except Exception as e:
    import traceback
    print('embed 失败:', type(e).__name__, str(e)[:500])
    traceback.print_exc()
