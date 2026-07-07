"""
阿里云 OSS 工具模块
- STS 临时凭证签发（前端直传）
- OSS 签名下载 URL 生成
- MD5 去重检查（上传前）
"""
import hashlib
import json
import logging
import os
import time
import uuid as _uuid
from datetime import datetime
from urllib.parse import quote

import oss2
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)


class OSSClient:
    """阿里云 OSS 操作封装"""

    def __init__(self):
        self.access_key_id = settings.OSS_ACCESS_KEY_ID
        self.access_key_secret = settings.OSS_ACCESS_KEY_SECRET
        raw_endpoint = (settings.OSS_ENDPOINT or '').strip()
        # 移除 protocol 前缀，用于 URL 拼接
        self.endpoint = raw_endpoint.replace('https://', '').replace('http://', '')
        # 保留完整 endpoint（含 protocol），传给 oss2 SDK
        self._raw_endpoint = raw_endpoint if '://' in raw_endpoint else f'https://{raw_endpoint}'
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
            self._bucket = oss2.Bucket(
                self.auth, self._raw_endpoint, self.bucket_name,
                connect_timeout=5,
                read_timeout=30,
                app_name='campus-resource',
            )
        return self._bucket

    def _sanitize_filename(self, file_name: str) -> str:
        """清理文件名，保留扩展名，替换不安全字符"""
        name, dot, ext = file_name.rpartition('.')
        safe_name = ''.join(c if c.isalnum() or c in '._-' else '_' for c in name)
        return f'{safe_name}.{ext}' if dot else safe_name

    def _generate_oss_key(self, file_name: str, scope: str = 'college',
                          owner_id: int = 0) -> str:
        """
        生成 OSS 对象 Key。
        格式: knowledge/{scope}/{owner_id}/{YYYY-MM}/{uuid}.{ext}

        Args:
            file_name: 原始文件名
            scope: 归属范围（school/college/department）
            owner_id: 归属对象 ID（college_id 或 department_id，school 时为 0）
        """
        ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'bin'
        now = datetime.now()
        safe_name = self._sanitize_filename(file_name)
        return f'knowledge/{scope}/{owner_id}/{now:%Y-%m}/{_uuid.uuid4().hex[:12]}.{ext}'

    # ------------------------------------------------------------------
    # STS 临时凭证（前端直传 OSS）
    # ------------------------------------------------------------------
    def get_sts_credentials(self, file_name: str, scope: str = 'college',
                            owner_id: int = 0) -> dict:
        """
        获取 STS 临时凭证，供前端直传 OSS。
        返回:
            {oss_key, upload_url, expiration, dev_mode}
            - dev_mode=True 时无真实 STS 凭证，使用签名直传 URL

        Args:
            file_name: 原始文件名
            scope: 归属范围（school/college/department）
            owner_id: 归属对象 ID
        """
        oss_key = self._generate_oss_key(file_name, scope, owner_id)

        if not self.access_key_id:
            # 本地开发：返回签名直传 URL（有效期 300 秒）
            upload_url = self.bucket.sign_url('PUT', oss_key, 300) if self.bucket else ''
            return {
                'oss_key': oss_key,
                'upload_url': upload_url,
                'expiration': int(time.time()) + 300,
                'dev_mode': True,
            }

        # 生产环境：调用 STS AssumeRole 获取临时凭证
        # NOTE: 需要安装 alibabacloud_sts20150401，STS Policy 限定上传路径前缀
        try:
            # from alibabacloud_sts20150401.client import Client as StsClient
            # from alibabacloud_sts20150401 import models as sts_models
            # sts_client = StsClient(...)
            # policy = {
            #     "Statement": [{
            #         "Effect": "Allow",
            #         "Action": ["oss:PutObject"],
            #         "Resource": [f"acs:oss:*:*:{self.bucket_name}/knowledge/{college_id}/*"]
            #     }],
            #     "Version": "1"
            # }
            # resp = sts_client.assume_role(...)
            # return {AccessKeyId, AccessKeySecret, SecurityToken, ...}
            upload_url = self.bucket.sign_url('PUT', oss_key, 300)
            return {
                'oss_key': oss_key,
                'upload_url': upload_url,
                'expiration': int(time.time()) + 300,
                'sts_credentials': None,  # 部署时替换为真实 STS 凭证
                'dev_mode': False,
            }
        except Exception as e:
            logger.error(f'STS 凭证获取失败: {e}')
            raise

    # ------------------------------------------------------------------
    # 签名下载 URL
    # ------------------------------------------------------------------
    def get_download_url(self, oss_key: str, expires: int = 300) -> str:
        """生成带签名的下载 URL，默认有效期 300 秒"""
        if not self.bucket:
            return ''
        try:
            return self.bucket.sign_url('GET', oss_key, expires)
        except Exception as e:
            logger.error(f'生成下载 URL 失败 oss_key={oss_key}: {e}')
            return ''

    def get_preview_url(self, oss_key: str, expires: int = 3600) -> str:
        """
        生成带签名的预览 URL（有效期 3600 秒，适合音频/视频播放）。
        与 get_download_url 相同机制，仅默认有效期更长。
        """
        return self.get_download_url(oss_key, expires)

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
        """
        检查 MD5 是否已存在，返回已有的 knowledge_doc 或 None。

        依赖 apps.knowledge.models.KnowledgeDoc（丙负责），
        若模型不存在则返回 None 降级运行。
        """
        try:
            from apps.knowledge.models import KnowledgeDoc
            return KnowledgeDoc.objects.filter(md5_hash=md5_hash).first()
        except ImportError:
            logger.warning('KnowledgeDoc 模型尚未就绪，跳过 MD5 去重检查')
            return None

    # ------------------------------------------------------------------
    # 文本直传（后端 PUT Object）
    # ------------------------------------------------------------------
    def upload_text(self, content: str, oss_key: str) -> bool:
        """
        将文本内容直接上传到 OSS（后端直传）。

        Args:
            content: 文本内容（字符串）
            oss_key: OSS 对象 Key

        Returns:
            True 表示上传成功，False 表示失败
        """
        if not self.bucket:
            logger.error('OSS Bucket 未初始化，无法上传文本')
            return False
        try:
            self.bucket.put_object(oss_key, content.encode('utf-8'))
            logger.info(f'文本上传成功 oss_key={oss_key}, size={len(content)} chars')
            return True
        except Exception as e:
            logger.error(f'OSS 文本上传失败 oss_key={oss_key}: {e}')
            return False

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
        except Exception as e:
            logger.error(f'OSS 文件删除失败 oss_key={oss_key}: {e}')
            return False


# 全局单例
oss_client = OSSClient()
