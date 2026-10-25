"""
大文件分片上传视图（基于 OSS Multipart Upload）

接口协议（与前端约定）：
- POST /api/knowledge/upload/chunk/init/  初始化：秒传检测 + 断点续传 + 生成 upload_id
- POST /api/knowledge/upload/chunk/       接收分片：流式转发 OSS，本地零落盘
- POST /api/knowledge/upload/chunk/merge/ 合并：OSS 服务端合并 + 创建知识文档记录

设计要点：
- 分片约定：前端 chunk_index 从 0 开始；OSS partNumber 从 1 开始，内部自动转换
- 单分片不小于 100KB（OSS 最小分片限制，最后一片除外），建议 5MB
- 分片状态存于 OSS 云端（list_parts），多实例部署无需 Redis
- 秒传基于 KnowledgeDoc.md5_hash，断点续传以 OSS list_parts 实际结果为准
"""
import json
import logging
import uuid
from datetime import datetime

import oss2
from drf_spectacular.utils import extend_schema
from rest_framework import parsers, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsAdminOrSuperAdmin
from utils.celery_dispatcher import safe_delay
from utils.error_codes import ErrorCode
from utils.oss import guess_content_type, oss_client

from .models import ChunkedUploadSession, Keyword, KnowledgeDoc
from .serializers import ALLOWED_FILE_TYPES, normalize_file_type
from .tasks import auto_link_relations_task, extract_freshness_task, index_document_task

logger = logging.getLogger(__name__)

# 上传会话过期时间（小时），超时由定时任务清理并 abort OSS 分片
SESSION_EXPIRE_HOURS = 24


def _build_oss_key(user, file_name: str) -> str:
    """按上传者角色生成 OSS Key，规则与 FileUploadView 保持一致"""
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'bin'
    safe_filename = f'{uuid.uuid4().hex[:12]}.{ext}'
    now = datetime.now()
    if user.role == 'super_admin':
        oss_scope, owner_id = 'school', 0
    elif user.role == 'dept_admin' and user.department_id:
        oss_scope, owner_id = 'department', user.department_id
    else:
        oss_scope, owner_id = 'college', user.college_id or 0
    return f'knowledge/{oss_scope}/{owner_id}/{now:%Y-%m}/{safe_filename}'


def _list_uploaded_parts(oss_key: str, upload_id: str) -> list:
    """列出 OSS 上已存在的分片 [(part_number, etag, size)]，按序号升序"""
    parts, marker = [], ''
    while True:
        result = oss_client.bucket.list_parts(oss_key, upload_id, marker=marker)
        parts.extend((p.part_number, p.etag, p.size) for p in result.parts)
        if not result.is_truncated:
            break
        marker = result.next_marker
    return parts


def _parse_keywords(raw) -> list:
    """兼容 list / JSON 字符串 / 逗号分隔字符串三种关键词传参"""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = [k.strip() for k in raw.split(',') if k.strip()]
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()][:20]
    return []


def _md5_visible_duplicate(request_user, duplicate_doc):
    """仅当重复文档对当前用户可见时才提示重复，避免泄露他人文档存在性"""
    return duplicate_doc and (
        request_user.role == 'super_admin'
        or duplicate_doc.uploader_id == request_user.id
        or duplicate_doc.visibility == 'public'
    )


@extend_schema(
    summary='分片上传-初始化', tags=['文件上传'], operation_id='分片上传初始化',
)
class ChunkUploadInitView(APIView):
    """
    POST /api/knowledge/upload/chunk/init/
    参数：file_md5 (str), filename (str), file_size (int, 可选)
    返回：
        秒传: {"status": "completed", "doc_id": 1, "duplicate": true}
        续传: {"status": "partial", "upload_id": "...", "uploaded_chunks": [0, 1, 3]}
        新传: {"status": "new", "upload_id": "...", "uploaded_chunks": []}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def post(self, request):
        file_md5 = str(request.data.get('file_md5', '')).strip().lower()
        filename = str(request.data.get('filename', '')).strip()
        try:
            file_size = int(request.data.get('file_size') or 0)
        except (TypeError, ValueError):
            file_size = 0

        if len(file_md5) != 32 or any(c not in '0123456789abcdef' for c in file_md5):
            raise ValidationError({'file_md5': 'MD5 格式不合法'})
        if not filename:
            raise ValidationError({'filename': '文件名不能为空'})

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'other'
        if ext not in ALLOWED_FILE_TYPES:
            raise ValidationError(
                {'file': f'不支持的文件类型 ".{ext}"，支持的类型：{", ".join(sorted(ALLOWED_FILE_TYPES))}'}
            )

        if not oss_client.bucket:
            return Response(
                {'detail': 'OSS 配置异常，请联系管理员', 'code': ErrorCode.OSS_CONFIG_ERROR.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 秒传：MD5 命中已有文档
        duplicate_doc = oss_client.check_duplicate(file_md5)
        if _md5_visible_duplicate(request.user, duplicate_doc):
            return Response({
                'status': 'completed',
                'doc_id': duplicate_doc.id,
                'duplicate': True,
                'file_name': duplicate_doc.file_name,
            })

        # 断点续传：查未完成会话 + OSS 实际已传分片
        session = ChunkedUploadSession.objects.filter(
            file_md5=file_md5, user=request.user, status='uploading',
        ).order_by('-created_at').first()

        if session:
            try:
                parts = _list_uploaded_parts(session.oss_key, session.upload_id)
                return Response({
                    'status': 'partial',
                    'upload_id': session.upload_id,
                    'uploaded_chunks': [p[0] - 1 for p in parts],
                })
            except oss2.exceptions.NoSuchUpload:
                session.status = 'aborted'
                session.save(update_fields=['status', 'updated_at'])
                session = None

        # 新建：初始化 OSS Multipart Upload
        oss_key = _build_oss_key(request.user, filename)
        try:
            result = oss_client.bucket.init_multipart_upload(
                oss_key, headers={'Content-Type': guess_content_type(filename)},
            )
        except oss2.exceptions.OssError as e:
            logger.error(f'初始化分片上传失败 oss_key={oss_key}: {e}')
            return Response(
                {'detail': '初始化上传失败', 'code': ErrorCode.OSS_SERVER_ERROR.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        session = ChunkedUploadSession.objects.create(
            upload_id=result.upload_id,
            user=request.user,
            file_name=filename,
            file_size=file_size,
            file_md5=file_md5,
            oss_key=oss_key,
        )
        return Response({'status': 'new', 'upload_id': session.upload_id, 'uploaded_chunks': []})


@extend_schema(
    summary='分片上传-接收分片', tags=['文件上传'], operation_id='分片上传接收分片',
)
class ChunkUploadView(APIView):
    """
    POST /api/knowledge/upload/chunk/ (multipart/form-data)
    参数：upload_id, chunk_index (从0开始), chunk_file (分片文件流)
    分片流式转发至 OSS UploadPart，不落本地磁盘；同一分片重复上传自动覆盖
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def post(self, request):
        upload_id = str(request.data.get('upload_id', '')).strip()
        chunk_index = request.data.get('chunk_index')
        chunk_file = request.FILES.get('chunk_file')

        if not upload_id or chunk_index is None or chunk_file is None:
            raise ValidationError({'detail': 'upload_id / chunk_index / chunk_file 均为必填'})
        try:
            chunk_index = int(chunk_index)
        except (TypeError, ValueError):
            raise ValidationError({'chunk_index': '分片序号必须为整数'})
        if not 0 <= chunk_index < 10000:
            raise ValidationError({'chunk_index': '分片序号超出范围 (0-9999)'})

        session = ChunkedUploadSession.objects.filter(
            upload_id=upload_id, user=request.user, status='uploading',
        ).first()
        if not session:
            return Response(
                {'detail': '上传会话不存在或已结束', 'code': ErrorCode.NOT_FOUND.value},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            result = oss_client.bucket.upload_part(
                session.oss_key, session.upload_id, chunk_index + 1, chunk_file,
            )
        except oss2.exceptions.OssError as e:
            logger.error(f'分片上传失败 upload_id={upload_id} chunk={chunk_index}: {e}')
            return Response(
                {'detail': '分片上传失败', 'code': ErrorCode.UPLOAD_FAILED.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({'status': 'success', 'chunk_index': chunk_index, 'etag': result.etag})


@extend_schema(
    summary='分片上传-合并', tags=['文件上传'], operation_id='分片上传合并',
)
class ChunkMergeView(APIView):
    """
    POST /api/knowledge/upload/chunk/merge/
    参数：upload_id, file_md5；可选 title / description / visibility /
          discipline_id / college_id / department_id / keywords / tags / relations
    OSS 服务端合并分片 → MD5 二次去重 → 创建 KnowledgeDoc → 触发异步索引
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def post(self, request):
        upload_id = str(request.data.get('upload_id', '')).strip()
        file_md5 = str(request.data.get('file_md5', '')).strip().lower()

        session = ChunkedUploadSession.objects.filter(
            upload_id=upload_id, user=request.user, status='uploading',
        ).first()
        if not session:
            return Response(
                {'detail': '上传会话不存在或已结束', 'code': ErrorCode.NOT_FOUND.value},
                status=status.HTTP_404_NOT_FOUND,
            )
        if file_md5 and file_md5 != session.file_md5:
            raise ValidationError({'file_md5': 'MD5 与初始化时不一致'})

        try:
            parts = _list_uploaded_parts(session.oss_key, session.upload_id)
        except oss2.exceptions.NoSuchUpload:
            return Response(
                {'detail': '上传会话已失效，请重新初始化', 'code': ErrorCode.UPLOAD_FAILED.value},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not parts:
            raise ValidationError({'detail': '没有任何分片，无法合并'})

        total_size = sum(p[2] for p in parts)
        if session.file_size and total_size != session.file_size:
            raise ValidationError(
                {'detail': f'分片总大小({total_size}B)与文件大小({session.file_size}B)不一致，可能存在分片缺失'}
            )

        # OSS 服务端合并
        try:
            oss_client.bucket.complete_multipart_upload(
                session.oss_key, session.upload_id, [(p[0], p[1]) for p in parts],
            )
        except oss2.exceptions.OssError as e:
            logger.error(f'合并分片失败 upload_id={upload_id}: {e}')
            return Response(
                {'detail': '合并分片失败', 'code': ErrorCode.OSS_SERVER_ERROR.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        session.status = 'completed'
        session.save(update_fields=['status', 'updated_at'])

        # 二次 MD5 去重
        duplicate_doc = oss_client.check_duplicate(session.file_md5)
        if _md5_visible_duplicate(request.user, duplicate_doc):
            oss_client.delete(session.oss_key)
            return Response({
                'id': duplicate_doc.id,
                'status': duplicate_doc.status,
                'duplicate': True,
            })

        # 创建知识文档记录
        ext = session.file_name.rsplit('.', 1)[-1].lower() if '.' in session.file_name else 'other'
        file_type = normalize_file_type(ext)
        visibility = request.data.get('visibility', 'public')
        if visibility not in ('public', 'private'):
            visibility = 'public'
        title = str(request.data.get('title', '')).strip() or (
            session.file_name.rsplit('.', 1)[0] if '.' in session.file_name else session.file_name
        )

        if request.user.role == 'super_admin':
            doc_college_id = request.data.get('college_id') or None
            doc_department_id = request.data.get('department_id') or None
        else:
            doc_college_id = request.user.college_id
            doc_department_id = request.user.department_id

        new_doc = KnowledgeDoc.objects.create(
            oss_key=session.oss_key,
            title=title,
            description=str(request.data.get('description', '')).strip(),
            file_name=session.file_name,
            file_size=total_size,
            file_type=file_type,
            md5_hash=session.file_md5,
            source_type='upload',
            uploader=request.user,
            status='uploaded',
            college_id=doc_college_id,
            department_id=doc_department_id,
            discipline_id=request.data.get('discipline_id') or None,
            visibility=visibility,
        )

        for phrase in _parse_keywords(request.data.get('keywords')):
            Keyword.objects.create(
                doc=new_doc, phrase=phrase,
                match_type='fuzzy', weight=1, created_by=request.user,
            )

        # 人工打标签（复用 views 中的工具函数，延迟导入避免循环依赖）
        from .views import _create_doc_tags, _normalize_tags
        _create_doc_tags(new_doc, _normalize_tags(request, request.data.get('tags')), request.user)

        safe_delay(extract_freshness_task, new_doc.id)
        safe_delay(index_document_task, new_doc.id)
        # AI 自动关联：上传后异步分析并直接关联到相关公开文档（用户无需手动选择、也看不到他人文档）
        safe_delay(auto_link_relations_task, new_doc.id)

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
            'file_name': session.file_name,
            'file_size': total_size,
        })
