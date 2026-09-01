# -*- coding: utf-8 -*-
"""OSS 连通性诊断脚本（region 扫描版）
- 用 backend/.env 的当前配置测试指定桶
- 多 region endpoint 扫描，判断桶的真实所在地/是否跨账号
用法: venv/Scripts/python.exe scripts/oss_diag.py
"""
import sys
import oss2
from decouple import Config, RepositoryEnv

ENV_PATH = sys.argv[1] if len(sys.argv) > 1 else '.env'
cfg = Config(RepositoryEnv(ENV_PATH))

ak = cfg('OSS_ACCESS_KEY_ID', default='')
sk = cfg('OSS_ACCESS_KEY_SECRET', default='')
bucket = cfg('OSS_BUCKET_NAME', default='campus-resourse')

print(f'AK={ak}  bucket={bucket}')

REGIONS = [
    'oss-cn-hangzhou.aliyuncs.com',
    'oss-cn-chengdu.aliyuncs.com',
    'oss-cn-beijing.aliyuncs.com',
    'oss-cn-shanghai.aliyuncs.com',
    'oss-cn-shenzhen.aliyuncs.com',
    'oss-cn-qingdao.aliyuncs.com',
    'oss-cn-hongkong.aliyuncs.com',
    'oss-cn-zhangjiakou.aliyuncs.com',
]


def probe(ep):
    auth = oss2.Auth(ak, sk)
    b = oss2.Bucket(auth, f'https://{ep}', bucket)
    try:
        info = b.get_bucket_info()
        return f'OK  region={info.location} owner={info.owner.id}'
    except oss2.exceptions.OssError as e:
        return f'status={e.status} code={e.code} msg={e.message}'
    except Exception as e:
        return f'{type(e).__name__}: {e}'


for ep in REGIONS:
    print(f'{ep:35s} -> {probe(ep)}')
