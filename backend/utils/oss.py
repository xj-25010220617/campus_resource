"""
阿里云 OSS 工具模块
- STS 临时凭证签发（前端直传）
- OSS 签名下载 URL 生成
- MD5 去重检查（上传前）
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from urllib.parse import quote

import oss2
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile


class OSSClient:
    """阿里云 OSS 操作封装"""

    def __init__(self):
        self.access_key_id = settings.OSS_ACCESS_KEY_ID
        self.access_key_secret = settings.OSS_ACCESS_KEY_SECRET
        self.endpoint = settings.OSS_ENDPOINT
        self.bucket_name = settings.OSS_BUCKET_NAME
        self._auth = None
        self._bucket = None

    @property
    def auth(self):
        if self._auth is None and self.access_key_id:
            self._auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        return self._auth

    @property
    def bucket(self):
        if self._bucket is None and self.auth:
            self._bucket = oss2.Bucket(self.auth, self.endpoint, self.bucket_name)
        return self._bucket

    def _generate_oss_key(self, file_name: str, college_code: str = '') -> str:
        """
        生成 OSS 对象 Key。
        格式: resources/{college_code}/{YYYY}/{MM}/{DD}/{uuid}.{ext}
        """
        ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'bin'
        now = datetime.now()
        prefix = f"resources/{college_code}" if college_code else "resources"
        return f"{prefix}/{now:%Y/%m/%d}/{now:%H%M%S}_{file_name}"

    # ------------------------------------------------------------------
    # STS 临时凭证（前端直传 OSS）
    # ------------------------------------------------------------------
    def get_sts_credentials(self, file_name: str, college_code: str = '') -> dict:
        """
        获取 STS 临时凭证，供前端直传 OSS。
        返回 {access_key_id, access_key_secret, security_token, expiration,
               oss_key, upload_url}
        """
        import uuid as _uuid
        oss_key = self._generate_oss_key(file_name, college_code)

        # STS 配置（使用阿里云 RAM 角色或 AssumeRole）
        # 开发阶段返回模拟凭证，部署时替换为真实 STS 调用
        if not self.access_key_id:
            # 本地开发：返回签名直传 URL
            from oss2.utils import make_sign_url
            upload_url = self.bucket.sign_url('PUT', oss_key, 600)
            return {
                'oss_key': oss_key,
                'upload_url': upload_url,
                'expiration': int(time.time()) + 600,
                'dev_mode': True,
            }

        # 生产环境：调用 AssumeRole 获取 STS
        # from alibabacloud_sts20150401.client import Client as StsClient
        # ... 真实 STS 调用
        upload_url = f"https://{self.bucket_name}.{self.endpoint}/{oss_key}"
        return {
            'oss_key': oss_key,
            'upload_url': upload_url,
            'expiration': int(time.time()) + 3600,
            'dev_mode': False,
        }

    # ------------------------------------------------------------------
    # 签名下载 URL
    # ------------------------------------------------------------------
    def get_download_url(self, oss_key: str, expires: int = 300) -> str:
        """生成带签名的下载 URL，默认有效期 300 秒"""
        if not self.bucket:
            return ''
        return self.bucket.sign_url('GET', oss_key, expires)

    # ------------------------------------------------------------------
    # MD5 去重检查
    # ------------------------------------------------------------------
    @staticmethod
    def compute_md5(file) -> str:
        """
        计算文件 MD5。
        支持: UploadedFile（Django）、BytesIO、本地文件路径
        """
        md5 = hashlib.md5()
        if isinstance(file, UploadedFile):
            for chunk in file.chunks():
                md5.update(chunk)
            file.seek(0)
        elif hasattr(file, 'read'):
            while True:
                chunk = file.read(8192)
                if not chunk:
                    break
                md5.update(chunk)
            file.seek(0)
        elif isinstance(file, str) and os.path.isfile(file):
            with open(file, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    md5.update(chunk)
        else:
            md5.update(str(file).encode('utf-8'))
        return md5.hexdigest()

    def check_duplicate(self, md5_hash: str):
        """检查 MD5 是否已存在，返回已有的 knowledge_doc 或 None"""
        from apps.knowledge.models import KnowledgeDoc
        return KnowledgeDoc.objects.filter(md5_hash=md5_hash).first()

    # ------------------------------------------------------------------
    # 文件删除
    # ------------------------------------------------------------------
    def delete(self, oss_key: str) -> bool:
        """删除 OSS 上的文件"""
        if not self.bucket:
            return False
        try:
            self.bucket.delete_object(oss_key)
            return True
        except Exception:
            return False


# 全局单例
oss_client = OSSClient()
