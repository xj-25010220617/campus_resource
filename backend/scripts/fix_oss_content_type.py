#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""存量 OSS 对象 Content-Type 修补脚本

背景：早期上传（后端直传 / OSS Multipart 分片）未写入对象 Content-Type，OSS 默认存为
application/octet-stream；而签名 URL 又不能覆盖 response-content-type（阿里云 OSS 不支持，
带了直接 400 InvalidRequest），导致图片/音视频/PDF 在浏览器里无法内联预览。
本脚本用 OSS 服务端自拷贝（x-oss-metadata-directive: REPLACE）把存量对象的 Content-Type
一次性写对，与 utils.oss.guess_content_type() 的规则保持一致。

覆盖对象：KnowledgeDoc / Resource 两张最终内容表（ChunkedUploadSession 为上传会话，不处理）。

用法（backend 目录下，读取 backend/.env 的 OSS 配置）：
    venv/bin/python scripts/fix_oss_content_type.py            # dry-run：只打印将修改的行
    venv/bin/python scripts/fix_oss_content_type.py --apply    # 真正执行修补
"""
import os
import sys

# 允许在任意目录直接运行本脚本：直跑 python scripts/xxx.py 时 sys.path[0] 是 scripts/，
# 需把 backend 根目录补进去才能 import config.* / apps.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

import oss2  # noqa: E402

from apps.knowledge.models import KnowledgeDoc  # noqa: E402
from apps.resources.models import Resource  # noqa: E402
from utils.oss import guess_content_type, oss_client  # noqa: E402

APPLY = '--apply' in sys.argv
TARGET_MODELS = [
    ('KnowledgeDoc', KnowledgeDoc),
    ('Resource', Resource),
]


def main() -> int:
    bucket = oss_client.bucket
    if bucket is None:
        print('[FAIL] OSS 未初始化，请确认 backend/.env 的 OSS_ACCESS_KEY_* 配置正确')
        return 1

    mode = 'APPLY' if APPLY else 'DRY-RUN'
    print(f'===== 存量对象 Content-Type 修补 [{mode}] bucket={oss_client.bucket_name} =====')

    total = 0          # 需修补
    already = 0        # 已是正确类型，跳过
    missing = 0        # OSS 上对象不存在
    failed = 0

    for label, model in TARGET_MODELS:
        qs = model.objects.exclude(oss_key='')
        print(f'\n--- {label}: {qs.count()} 个带 oss_key 的对象 ---')
        for obj in qs.iterator(chunk_size=200):
            file_name = getattr(obj, 'file_name', '') or ''
            oss_key = obj.oss_key
            desired = guess_content_type(file_name)
            try:
                head = bucket.head_object(oss_key)
                hdrs = head.headers or {}
                current = hdrs.get('Content-Type') or hdrs.get('content-type') or ''
            except oss2.exceptions.NoSuchKey:
                missing += 1
                print(f'  [MISS] id={obj.id:<8} {oss_key}  OSS 上不存在，跳过')
                continue
            except oss2.exceptions.OssError as e:
                failed += 1
                print(f'  [FAIL] id={obj.id:<8} {oss_key}  head 失败 status={e.status} code={e.code}')
                continue

            current_norm = current.split(';')[0].strip().lower()
            if current_norm == desired or (desired == 'application/octet-stream' and not file_name):
                already += 1
                continue

            total += 1
            print(f'  [FIX ] id={obj.id:<8} file={file_name[:40]!r:<44} '
                  f'Content-Type: {current!r} -> {desired}')
            if not APPLY:
                continue
            try:
                bucket.copy_object(
                    bucket.bucket_name, oss_key, oss_key,
                    headers={'x-oss-metadata-directive': 'REPLACE', 'Content-Type': desired},
                )
            except oss2.exceptions.OssError as e:
                failed += 1
                print(f'  [FAIL] id={obj.id:<8} {oss_key}  自拷贝失败 status={e.status} code={e.code}')

    print('\n===== 汇总 =====')
    print(f'需修补: {total}   已是正确类型: {already}   对象不存在: {missing}   失败: {failed}')
    if not APPLY:
        print('（dry-run 未真正修改；确认无误后加 --apply 执行）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
