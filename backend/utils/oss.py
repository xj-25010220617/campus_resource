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
from typing import Optional
from urllib.parse import quote

import oss2
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)


# 扩展名 → Content-Type 映射。对象上传时写入 OSS 元数据，供浏览器内联预览。
# 注意：阿里云 OSS **不支持**签名 URL 上的 response-content-type 参数
# （会返回 400 InvalidRequest: Can not override response header on content-type），
# 因此 Content-Type 必须在 put_object / multipart 上传时就写对，不能事后覆盖。
MIME_MAP = {
    # 图片
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp',
    'svg': 'image/svg+xml', 'ico': 'image/x-icon', 'tiff': 'image/tiff', 'tif': 'image/tiff',
    'psd': 'image/vnd.adobe.photoshop',
    # 音频
    'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
    'flac': 'audio/flac', 'aac': 'audio/aac', 'm4a': 'audio/mp4', 'amr': 'audio/amr',
    # 视频
    'mp4': 'video/mp4', 'm4v': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
    'avi': 'video/x-msvideo', 'mkv': 'video/x-matroska', 'flv': 'video/x-flv',
    # 文档
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    # 文本 / 数据
    'txt': 'text/plain', 'md': 'text/markdown',
    'html': 'text/html', 'htm': 'text/html',
    'csv': 'text/csv', 'json': 'application/json', 'xml': 'application/xml',
    'yaml': 'text/yaml', 'yml': 'text/yaml',
    # 压缩包 / 其它
    'zip': 'application/zip', 'rar': 'application/x-rar-compressed', '7z': 'application/x-7z-compressed',
    'epub': 'application/epub+zip', 'pub': 'application/x-mspublisher',
    'ai': 'application/postscript', 'stl': 'model/stl', 'obj': 'model/obj',
}

DEFAULT_MIME_TYPE = 'application/octet-stream'


# ---------------------------------------------------------------------------
# OSS 命名空间（P1-2A 引入）
#
# quarantine/ 与 knowledge/ 是**平级**的顶层前缀，不是 knowledge/quarantine/。
# 原因：knowledge/ 未来要收紧为"已完成 promotion 的正式内容命名空间"，
# 而 P1-1 的 scope 校验（上传回调校验 oss_key 必须为 knowledge/{scope}/{owner}/）
# 也不接受 knowledge/quarantine/ 这种结构。
# ---------------------------------------------------------------------------

# 隔离区：尚未通过安全审核的对象
QUARANTINE_KEY_PREFIX = 'quarantine/'
# 正式区：已完成 promotion 的对象。
# 必须与 apps.knowledge.moderation.INDEXABLE_OSS_KEY_PREFIX 保持一致
# （由 apps/knowledge/tests.py 中的一致性测试守护）。
OFFICIAL_KEY_PREFIX = 'knowledge/'

# 流式读取默认值
DEFAULT_STREAM_CHUNK_SIZE = 64 * 1024
# 单次流式读取的字节上限，与 MAX_FILE_SIZE（500MB）对齐
DEFAULT_MAX_STREAM_BYTES = 500 * 1024 * 1024


def is_official_oss_key(oss_key) -> bool:
    """对象是否位于**正式区**（`knowledge/`）命名空间。

    P1-2D STEP 4-A 起，新上传的对象一律先落在 `quarantine/`，只有经过审核
    （P1-2C `moderate_document()`）并明确 approved、再由 `promote_object()`
    提升之后，才会出现在 `knowledge/` 下。因此"对象在正式区"是**对外提供内容**
    的必要条件：下载 / 预览等会签发 URL 的接口必须先用本函数把关，
    否则隔离区里未审核的内容会被直接取回。

    这里只做命名空间判断，不做审核状态判断——审核结论属于 ContentModeration，
    由后续阶段（STEP 4-B / P1-2E）叠加。
    """
    return bool(oss_key) and str(oss_key).startswith(OFFICIAL_KEY_PREFIX)


def build_quarantine_key(scope: str, owner_id: int, file_name: str) -> str:
    """生成隔离区 OSS Key：`quarantine/{scope}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}`

    与 `knowledge/` 下的正式 Key 保持相同的分片结构，只换顶层前缀，
    便于 promotion 时按相同尾部路径计算目标 Key。

    Args:
        scope: 归属范围（school / college / department）
        owner_id: 归属对象 ID（school 时为 0）
        file_name: 原始文件名（仅用于取扩展名）

    Returns:
        形如 `quarantine/college/12/2026-09/550e8400e29b.pdf` 的 OSS Key

    Note:
        本函数**只生成 Key**：不写 OSS、不建数据库记录。真正调用它的是后续阶段的
        上传流水线（P1-2D）。

        这里刻意**不复用** `OSSClient._generate_oss_key`——后者的 `knowledge/` 前缀
        是写死的，为它加参数会触及 P1-1 的上传链路；隔离区 Key 独立生成、
        互不影响，是更安全的选择。
    """
    ext = file_name.rsplit('.', 1)[-1].lower() if file_name and '.' in file_name else 'bin'
    now = datetime.now()
    return f'{QUARANTINE_KEY_PREFIX}{scope}/{owner_id}/{now:%Y-%m}/{_uuid.uuid4().hex[:12]}.{ext}'


def build_promoted_key(quarantine_key: str) -> str:
    """由隔离区 Key 推出正式区 Key：`quarantine/` → `knowledge/`，**只换顶层前缀**。

    与 `build_quarantine_key()` 互为逆操作：promotion 之后对象的
    `scope / owner_id / YYYY-MM / uuid.ext` 全部保持原样，只有命名空间改变。

    安全约束（不提供绕过开关）：
    - 输入**必须**以 `quarantine/` 开头，否则抛异常
    - 前缀之后必须是恰好 4 段非空路径（`{scope}/{owner}/{YYYY-MM}/{file}`），
      与 `build_quarantine_key()` 生成的结构一致；段内不允许 `.` / `..`
    - 拒绝 `..`、绝对路径、反斜杠、控制字符、空 Key

    因此以下输入一律被拒绝（不会"猜测性地"转换）：
        ''                       —— 空
        'random/path/file.pdf'   —— 前缀不对
        'knowledge-malicious/x'  —— 前缀不对
        'knowledge/college/1/2026-09/a.pdf'  —— 已是正式区
        'quarantine/'            —— 没有剩余路径
        'quarantine/../knowledge/x'          —— 路径穿越
        'quarantine/a/b/c'       —— 段数不足

    Args:
        quarantine_key: 隔离区对象 Key

    Returns:
        形如 `knowledge/college/12/2026-09/550e8400e29b.pdf` 的正式区 Key

    Raises:
        OSSOperationError: 输入不合法

    Note:
        本函数**只做字符串变换**：不读 OSS、不校验对象是否存在、不写数据库。
        "目标对象真实存在且与源一致"由 `OSSClient.promote_object()` 的
        copy + HEAD 校验负责，二者不可互相替代。
    """
    key = (quarantine_key or '').strip() if isinstance(quarantine_key, str) else ''
    if not key:
        raise OSSOperationError('promotion 源 Key 为空')
    if not key.startswith(QUARANTINE_KEY_PREFIX):
        raise OSSOperationError(
            f'promotion 源必须位于 {QUARANTINE_KEY_PREFIX} 下，实际: {quarantine_key!r}'
        )
    # 控制字符 / 反斜杠：合法 Key 只由 build_quarantine_key 生成，不会含这些字符
    if any(ch in key for ch in ('\\', '\r', '\n', '\x00')) or key.startswith('/'):
        raise OSSOperationError(f'promotion 源 Key 含非法字符: {quarantine_key!r}')

    suffix = key[len(QUARANTINE_KEY_PREFIX):]
    if not suffix:
        raise OSSOperationError(f'promotion 源 Key 缺少路径部分: {quarantine_key!r}')

    segments = suffix.split('/')
    if len(segments) != 4 or any(
        not seg or seg in ('.', '..') for seg in segments
    ):
        raise OSSOperationError(
            f'promotion 源 Key 路径结构不合法（应为 '
            f'{{scope}}/{{owner}}/{{YYYY-MM}}/{{file}}）: {quarantine_key!r}'
        )
    if '..' in key:
        raise OSSOperationError(f'promotion 源 Key 含路径穿越: {quarantine_key!r}')

    dst_key = OFFICIAL_KEY_PREFIX + suffix
    # 兜底：变换结果必须落在正式区且通过 OSS 原语的 Key 安全检查
    if not is_official_oss_key(dst_key) or not OSSClient._is_safe_key(dst_key):
        raise OSSOperationError(f'promotion 目标 Key 非法: {dst_key!r}')
    return dst_key


class OSSOperationError(Exception):
    """OSS 原语操作失败。

    与 OSSClient 中其它"失败时降级返回 None / False"的方法不同，P1-2A 新增的
    copy / stream / promote 原语位于审核流水线的安全关键路径上：调用方必须能
    把"确认成功"与"不确定/失败"区分开。把失败静默降级成返回值，会让上层
    "复制没成功但以为成功了"，因此这里显式抛异常。
    """


def guess_content_type(file_name: str) -> str:
    """按扩展名推断 Content-Type；空/未知一律返回 application/octet-stream。

    支持直接传原始文件名（如 "校园平面图.jpg"），也支持传纯扩展名（"jpg"）。
    """
    if not file_name:
        return DEFAULT_MIME_TYPE
    ext = file_name.rsplit('.', 1)[-1].strip().lower()
    return MIME_MAP.get(ext, DEFAULT_MIME_TYPE)


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
                            owner_id: int = 0, oss_key: str = None) -> dict:
        """
        获取 STS 临时凭证，供前端直传 OSS。
        返回:
            {oss_key, upload_url, expiration, dev_mode}
            - dev_mode=True 时无真实 STS 凭证，使用签名直传 URL

        Args:
            file_name: 原始文件名
            scope: 归属范围（school/college/department）
            owner_id: 归属对象 ID
            oss_key: **显式指定**要签发的对象 Key（P1-2D STEP 4-A 起由
                UploadIntent 提供，落在 `quarantine/`）。为 None 时沿用旧的
                `_generate_oss_key()` 行为（`knowledge/` 前缀）以保持兼容。
        """
        if oss_key is None:
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
    def _build_content_disposition(self, file_name: str) -> str:
        """
        构造 Content-Disposition 头值，使下载的文件使用原始文件名。

        上传时对象名是随机 UUID（见 _generate_oss_key），OSS 默认按对象名保存，
        导致下载文件名与原始文件名不一致。这里通过 response-content-disposition
        响应头覆盖，让浏览器以原始文件名保存：
            - filename="..."  ASCII 回退（兼容旧浏览器）
            - filename*=UTF-8''...  RFC 5987 编码（支持中文等非 ASCII 文件名）
        """
        # ASCII 回退：仅保留可打印 ASCII，去掉引号/分号/反斜杠/路径分隔符等危险字符
        ascii_name = ''.join(
            c for c in file_name if 32 <= ord(c) < 127 and c not in '";\\/'
        ).strip() or 'download'
        # RFC 5987：整名 UTF-8 百分号编码（safe='' 连 '/' 一起转义，防路径注入）
        encoded_name = quote(file_name, safe='')
        return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'

    def get_download_url(self, oss_key: str, expires: int = 300,
                         file_name: str = None) -> str:
        """
        生成带签名的下载 URL，默认有效期 300 秒。

        Args:
            oss_key: OSS 对象 Key
            expires: 有效期（秒）
            file_name: 原始文件名。传入时通过 response-content-disposition
                响应头覆盖，使下载文件以原始文件名保存（否则为 UUID 对象名）。
                预览场景（<img>/<video> 内联播放）不要传，避免触发附件下载。
        """
        if not self.bucket:
            return ''
        params = None
        if file_name:
            params = {
                'response-content-disposition': self._build_content_disposition(file_name),
            }
        try:
            return self.bucket.sign_url('GET', oss_key, expires, params=params)
        except Exception as e:
            logger.error(f'生成下载 URL 失败 oss_key={oss_key}: {e}')
            return ''

    def get_preview_url(self, oss_key: str, expires: int = 3600) -> str:
        """
        生成带签名的预览 URL（有效期 3600 秒，适合音频/视频播放）。
        与 get_download_url 相同机制，仅默认有效期更长。
        不携带 Content-Disposition 覆盖，浏览器内联显示（PDF/图片/音视频）。

        注意：阿里云 OSS **不支持** response-content-type 参数——签名 URL 带上它会返回
        400 InvalidRequest: Can not override response header on content-type。
        因此这里不做 MIME 覆盖，对象的 Content-Type 必须在**上传时**写对
        （见 guess_content_type()），否则浏览器会按 application/octet-stream 处理。

        Args:
            oss_key: OSS 对象 Key
            expires: 有效期（秒）
        """
        if not self.bucket:
            return ''
        try:
            return self.bucket.sign_url('GET', oss_key, expires)
        except Exception as e:
            logger.error(f'生成预览 URL 失败 oss_key={oss_key}: {e}')
            return ''

    # ------------------------------------------------------------------
    # 对象元数据（服务端权威事实）
    # ------------------------------------------------------------------
    def head_object(self, oss_key: str) -> Optional[dict]:
        """
        读取 OSS 对象的服务端权威元数据（只发 HEAD 请求，不下载内容）。

        用于上传回调等场景校验客户端声明值——客户端的 oss_key / file_size /
        content-type 都是不可信输入，必须以 OSS 侧的实际状态为准。

        Args:
            oss_key: OSS 对象 Key

        Returns:
            None               — OSS 不可用或查询失败（**无法验证**，调用方应拒绝）
            {'exists': False}  — 对象不存在
            {'exists': True, 'size': int, 'content_type': str, 'etag': str}

        Note:
            本函数只能证明"对象存在且元数据为何"，**不能**证明该对象由谁上传。
            上传归属校验需要服务端上传凭证记录，当前数据模型不具备。
        """
        if not self.bucket:
            logger.error(f'OSS Bucket 未初始化，无法读取对象元数据 oss_key={oss_key}')
            return None
        missing = {'exists': False, 'size': 0, 'content_type': '', 'etag': ''}
        try:
            result = self.bucket.head_object(oss_key)
        except oss2.exceptions.NoSuchKey:
            return missing
        except oss2.exceptions.OssError as e:
            # 不同 OSS 版本对 404 的异常类型不一致，按 status 兜底判断
            if getattr(e, 'status', None) == 404:
                return missing
            logger.error(f'读取对象元数据失败 oss_key={oss_key}: {e}')
            return None
        except Exception as e:
            logger.error(f'读取对象元数据异常 oss_key={oss_key}: {e}')
            return None

        headers = result.headers or {}
        content_type = headers.get('Content-Type') or headers.get('content-type') or ''
        return {
            'exists': True,
            'size': int(result.content_length or 0),
            'content_type': str(content_type),
            # 简单 PUT 的 ETag 即对象 MD5；分片上传形如 "<md5>-<n>"，不能直接比对
            'etag': str(result.etag or '').strip('"'),
        }

    # ------------------------------------------------------------------
    # 服务端复制 / 流式读取 / promotion（P1-2A）
    # ------------------------------------------------------------------
    @staticmethod
    def _is_safe_key(key: str) -> bool:
        """对象 Key 是否不含路径穿越等危险形态（与 P1-1 的校验口径一致）。"""
        if not key or not isinstance(key, str):
            return False
        if key.startswith('/') or '..' in key:
            return False
        return True

    def copy_object(self, src_key: str, dst_key: str,
                    content_type: str = None) -> dict:
        """服务端复制 OSS 对象（不下载到本机、不占用本机带宽）。

        Args:
            src_key: 源对象 Key
            dst_key: 目标对象 Key
            content_type: 传入时覆盖目标对象的 Content-Type；
                不传则沿用源对象的元数据（OSS 默认行为）

        Returns:
            目标对象复制后的**实际**元数据（复制后重新 HEAD 得到，非推测值）：
            {'src_key', 'dst_key', 'size', 'etag', 'content_type'}

        Raises:
            OSSOperationError: Bucket 未初始化 / Key 非法 / 复制失败 /
                复制后无法确认目标对象存在
        """
        if not self.bucket:
            raise OSSOperationError('OSS Bucket 未初始化，无法复制对象')
        if not self._is_safe_key(src_key) or not self._is_safe_key(dst_key):
            raise OSSOperationError(f'OSS Key 非法 src={src_key!r} dst={dst_key!r}')

        headers = None
        if content_type:
            # OSS 默认 x-oss-metadata-directive=COPY，会连 Content-Type 一起复制；
            # 要覆盖 Content-Type 必须显式改为 REPLACE，否则传入的值会被忽略。
            headers = {
                'Content-Type': content_type,
                'x-oss-metadata-directive': 'REPLACE',
            }
        try:
            self.bucket.copy_object(self.bucket_name, src_key, dst_key, headers=headers)
        except Exception as e:
            logger.error(f'OSS 复制对象失败 {src_key} -> {dst_key}: {e}')
            raise OSSOperationError(f'OSS 复制对象失败: {e}') from e

        meta = self.head_object(dst_key)
        if meta is None:
            raise OSSOperationError(f'复制后无法确认目标对象元数据 dst_key={dst_key}')
        if not meta.get('exists'):
            raise OSSOperationError(f'复制后目标对象不存在 dst_key={dst_key}')
        return {
            'src_key': src_key,
            'dst_key': dst_key,
            'size': meta['size'],
            'etag': meta['etag'],
            'content_type': meta['content_type'],
        }

    def iter_object_chunks(self, oss_key: str,
                           chunk_size: int = DEFAULT_STREAM_CHUNK_SIZE,
                           max_bytes: int = DEFAULT_MAX_STREAM_BYTES):
        """流式读取 OSS 对象内容，逐块产出（**不**把整个对象读进内存）。

        供后续审核链路使用（例如 ClamAV INSTREAM，或流式落盘给解析器）。
        替代「`get_download_url` + `resp.content`」那种整文件进内存的读法。

        Args:
            oss_key: 对象 Key
            chunk_size: 单次读取字节数，默认 64KB
            max_bytes: 允许读取的字节上限，默认 500MB。超过即抛异常终止，
                而不是静默截断——截断会让调用方拿到"不完整但看起来正常"的内容，
                审核场景下比直接失败更危险。

        Yields:
            bytes：对象内容分块

        Raises:
            OSSOperationError: Bucket 未初始化 / 参数非法 / 打开对象失败 /
                读取中断 / 超过 max_bytes
        """
        if not self.bucket:
            raise OSSOperationError('OSS Bucket 未初始化，无法读取对象')
        if not self._is_safe_key(oss_key):
            raise OSSOperationError(f'OSS Key 非法 oss_key={oss_key!r}')
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            raise OSSOperationError(f'chunk_size 必须为正整数，实际: {chunk_size!r}')
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise OSSOperationError(f'max_bytes 必须为正整数（禁止无上限读取），实际: {max_bytes!r}')

        try:
            resp = self.bucket.get_object(oss_key)
        except Exception as e:
            logger.error(f'OSS 打开对象失败 oss_key={oss_key}: {e}')
            raise OSSOperationError(f'OSS 打开对象失败: {e}') from e

        total = 0
        try:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OSSOperationError(
                        f'对象超过单次读取上限 max_bytes={max_bytes} oss_key={oss_key}'
                    )
                yield chunk
        except OSSOperationError:
            raise
        except Exception as e:
            logger.error(f'OSS 流式读取中断 oss_key={oss_key}: {e}')
            raise OSSOperationError(f'OSS 流式读取中断: {e}') from e
        finally:
            try:
                resp.close()
            except Exception as e:
                logger.warning(f'关闭 OSS 读取流失败 oss_key={oss_key}: {e}')

    def promote_object(self, src_key: str, dst_key: str,
                       content_type: str = None) -> dict:
        """把隔离区对象提升为正式区对象：copy + HEAD 校验。

        硬编码的安全约束（不提供绕过开关）：
        - 源必须在 quarantine/ 下
        - 目标必须在 knowledge/ 下

        因此 `knowledge/ → quarantine/` 不会被当作 promotion，
        `foo/ → knowledge/` 也会被拒绝。

        **本方法不删除源对象**：`quarantine → knowledge` 之后是否清理隔离对象，
        由后续的 promotion 状态机与清理任务统一决定。在基础原语里做
        copy+delete，会把"复制成功、删除失败"的双对象状态变成一个难以处理的
        隐式中途状态。

        **本方法不修改任何数据库记录**：不建 KnowledgeDoc、不改审核状态、
        不触发索引。这些全部属于后续 pipeline。

        Args:
            src_key: 隔离区对象 Key（须以 quarantine/ 开头）
            dst_key: 正式区对象 Key（须以 knowledge/ 开头）
            content_type: 传入时覆盖目标对象的 Content-Type

        Returns:
            {'src_key', 'dst_key', 'size', 'etag', 'content_type',
             'src_size', 'verified': True}
            其中 size 为复制后实测的目标对象大小，src_size 为源对象实测大小，
            二者相等才会返回（否则抛异常）。

        Raises:
            OSSOperationError: 前缀不符 / 源对象不存在 / 复制失败 / 大小校验不一致
        """
        if not src_key or not src_key.startswith(QUARANTINE_KEY_PREFIX):
            raise OSSOperationError(
                f'promotion 源必须位于 {QUARANTINE_KEY_PREFIX} 下，实际: {src_key!r}'
            )
        if not dst_key or not dst_key.startswith(OFFICIAL_KEY_PREFIX):
            raise OSSOperationError(
                f'promotion 目标必须位于 {OFFICIAL_KEY_PREFIX} 下，实际: {dst_key!r}'
            )
        if not self._is_safe_key(src_key) or not self._is_safe_key(dst_key):
            raise OSSOperationError(f'OSS Key 非法 src={src_key!r} dst={dst_key!r}')

        src_meta = self.head_object(src_key)
        if src_meta is None:
            raise OSSOperationError(f'无法读取源对象元数据 src_key={src_key}')
        if not src_meta.get('exists'):
            raise OSSOperationError(f'源对象不存在 src_key={src_key}')

        result = self.copy_object(src_key, dst_key, content_type=content_type)
        if result['size'] != src_meta['size']:
            raise OSSOperationError(
                f'promotion 校验失败：目标对象大小与源不一致 '
                f'(src={src_meta["size"]}, dst={result["size"]}) dst_key={dst_key}'
            )
        result['src_size'] = src_meta['size']
        result['verified'] = True
        return result

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
            self.bucket.put_object(
                oss_key, content.encode('utf-8'),
                headers={'Content-Type': 'text/plain; charset=utf-8'},
            )
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
