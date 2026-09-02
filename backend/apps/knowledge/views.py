"""Knowledge - 知识文档与关键词视图

包含：文档 CRUD、上传（STS 回调 / 文本录入 / 文件直传）、预览、关键词管理。
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import django_filters
import requests

import oss2

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema

from rest_framework import filters, generics, parsers, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import College
from apps.classify.models import Department, Discipline
from apps.resources.models import UploadLog
from config.permissions import CanUploadDocument, IsAdminOrSuperAdmin, IsLoginUser
from utils.oss import oss_client
from utils.error_codes import ErrorCode
from utils.parsers import CleanJSONParser

from .extractor import extract_freshness_for_doc
from .models import Keyword, KnowledgeDoc
from .serializers import (
    AIClassifySerializer,
    ALLOWED_FILE_TYPES,
    FILE_SIZE_LIMITS,
    FileUploadSerializer,
    KeywordCreateSerializer,
    KeywordSerializer,
    KnowledgeDocListSerializer,
    KnowledgeDocSerializer,
    normalize_file_type,
    TextUploadSerializer,
    UploadCallbackSerializer,
    UploadSTSRequestSerializer,
)


logger = logging.getLogger(__name__)


class KnowledgeDocFilter(django_filters.FilterSet):
    """知识文档多条件精确过滤器"""
    college_id = django_filters.NumberFilter(field_name='college_id')
    department_id = django_filters.NumberFilter(field_name='department_id')
    discipline_id = django_filters.NumberFilter(field_name='discipline_id')
    file_type = django_filters.CharFilter(field_name='file_type')
    status = django_filters.CharFilter(field_name='status')
    source_type = django_filters.CharFilter(field_name='source_type')
    days_back = django_filters.NumberFilter(method='filter_days_back')

    class Meta:
        model = KnowledgeDoc
        fields = [
            'college_id', 'department_id', 'discipline_id',
            'file_type', 'status', 'source_type', 'days_back',
        ]

    def filter_days_back(self, queryset, name, value):
        """按时间范围过滤，days_back=0 表示不限时间"""
        if value and value > 0:
            since_date = timezone.now() - timedelta(days=value)
            return queryset.filter(created_at__gte=since_date)
        return queryset


@extend_schema(
    summary='知识文档列表',
    description='分页返回知识文档列表，支持关键词搜索和多条件组合筛选',
    tags=['知识库管理'],
    operation_id='知识文档列表',
)
class KnowledgeDocListView(generics.ListAPIView):
    """
    知识文档列表接口

    GET /api/knowledge/docs/
    功能：分页返回知识文档列表，支持关键词搜索和多条件组合筛选
    请求参数：
        - page (int, default=1): 页码
        - page_size (int, default=20): 每页数量
        - search (str, optional): 关键词搜索，模糊匹配文档标题（title）和描述（description）
        - college_id (int, optional): 按学院ID精确筛选
        - discipline_id (int, optional): 按学科方向ID精确筛选
        - file_type (str, optional): 按文件类型精确筛选（pdf/doc/ppt/xls/image/video/audio/md/txt/html/other）
        - status (str, optional): 按状态精确筛选（uploaded/parsing/chunking/embedding/indexed/failed）
        - source_type (str, optional): 按来源类型精确筛选（upload/website/library）
    权限规则：
        - super_admin（超级管理员）: 可查看平台全部文档
        - admin（学院管理员）: 仅能查看自己所属学院下的所有文档
        - 普通登录用户: 仅能查看本人自己上传的文档
    返回：DRF 分页包装后的文档简化列表，
          每条包含 id / title / file_type / file_size / status /
          college_name / discipline_name / uploader_name / chunk_count / created_at
    """
    serializer_class = KnowledgeDocListSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = KnowledgeDocFilter
    search_fields = ['title', 'description']

    def get_queryset(self):
        user = self.request.user

        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'uploader',
            'uploader__college', 'uploader__department',
        ).all()

        # 可见性权限过滤
        queryset = self._admin_scope_filter(queryset, user)

        # 默认时间范围过滤：近一年（days_back=0 表示不限时间）
        days_back = self.request.query_params.get('days_back', '365')
        try:
            days_back = int(days_back)
        except (ValueError, TypeError):
            days_back = 365

        if days_back > 0:
            since_date = timezone.now() - timedelta(days=days_back)
            queryset = queryset.filter(created_at__gte=since_date)

        return queryset.order_by('-created_at')

    @staticmethod
    def _admin_scope_filter(queryset, user):
        """管理后台权限过滤（通过 college_id/department_id 判断归属）：
        - super_admin：全量
        - college_admin / admin：校级文档 + 本院文档（含部门级） + 自己上传的
        - dept_admin：校级文档 + 本部门文档 + 自己上传的
        - user：仅本人上传的文档（对齐接口文档「普通用户仅能查看/下载自己上传的文档」，
          避免泄露他人私密/未完成文档）
        """
        # 校级文档：college_id 和 department_id 均为 null
        SCHOOL_LEVEL = Q(college_id__isnull=True, department_id__isnull=True)

        if user.role == 'super_admin':
            return queryset
        if user.role in ('college_admin', 'admin'):
            return queryset.filter(
                SCHOOL_LEVEL
                | Q(college_id=user.college_id)
                | Q(uploader_id=user.id),
            )
        if user.role == 'dept_admin':
            return queryset.filter(
                SCHOOL_LEVEL
                | Q(department_id=user.department_id)
                | Q(uploader_id=user.id),
            )
        # user（普通用户）：仅本人上传
        return queryset.filter(uploader_id=user.id)

    @staticmethod
    def _rag_scope_filter(queryset, user_context):
        """AI 检索权限过滤（通过 college_id/department_id 判断归属）：
        - private 文档绝不检索
        - super_admin：所有 public 文档
        - college_admin / admin：校级文档 + 本院文档（含部门级） + 自己上传
        - dept_admin：校级文档 + 本部门文档 + 自己上传
        - user：校级文档 + 本院文档 + 本部门文档 + 自己上传
        """
        # 校级文档：college_id 和 department_id 均为 null
        SCHOOL_LEVEL = Q(college_id__isnull=True, department_id__isnull=True)

        role = user_context.get('role', 'user')
        user_id = user_context.get('user_id')
        college_id = user_context.get('college_id')
        dept_id = user_context.get('department_id')
        qs = queryset.filter(visibility='public')
        if role == 'super_admin':
            return qs
        if role in ('college_admin', 'admin'):
            conditions = SCHOOL_LEVEL
            if college_id:
                conditions |= Q(college_id=college_id)
            if user_id:
                conditions |= Q(uploader_id=user_id)
            return qs.filter(conditions)
        if role == 'dept_admin':
            conditions = SCHOOL_LEVEL
            if dept_id:
                conditions |= Q(department_id=dept_id)
            if user_id:
                conditions |= Q(uploader_id=user_id)
            return qs.filter(conditions)
        # user（普通用户）
        conditions = SCHOOL_LEVEL
        if college_id:
            conditions |= Q(college_id=college_id)
        if dept_id:
            conditions |= Q(department_id=dept_id)
        if user_id:
            conditions |= Q(uploader_id=user_id)
        return qs.filter(conditions)


@extend_schema(
    summary='知识文档详情',
    description='获取文档完整信息或编辑文档元数据',
    tags=['知识库管理'],
    operation_id='知识文档详情',
)
class KnowledgeDocDetailView(generics.RetrieveUpdateAPIView):
    """
    知识文档详情与编辑接口

    GET /api/knowledge/docs/{id}/
    功能：返回指定文档的完整信息，含所有字段及展平的关联名称

    PUT /api/knowledge/docs/{id}/
    功能：编辑文档元数据（按需传入字段，仅更新传入的字段）
    请求参数（均可选，按需传入）：
        - title (str, optional): 文档标题
        - description (str, optional): 文档描述
        - discipline_id (int, optional): 学科方向 ID
        - freshness_date (str, optional): 内容截止日期（格式 YYYY-MM-DD）
    权限规则：
        - super_admin（超级管理员）: 可查看/编辑平台全部文档
        - admin（学院管理员）: 仅能查看/编辑自己所属学院下的文档
        - 普通登录用户: 仅能查看/编辑本人上传的文档
    返回：
        - GET: 文档完整对象，包含 id / title / description / source_type / source_url /
          oss_key / oss_url / file_name / file_type / file_size / md5_hash / page_count /
          discipline / discipline_name /
          college / college_name / uploader / uploader_name /
          status / chunk_count / is_sensitive / sensitive_checked /
          freshness_date / created_at / updated_at / indexed_at
        - PUT: 更新后的文档完整对象
    """
    serializer_class = KnowledgeDocSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'uploader',
        ).all()
        return KnowledgeDocListView._admin_scope_filter(queryset, user)

    def perform_update(self, serializer):
        """仅允许超管或文档所有者编辑"""
        instance = self.get_object()
        if self.request.user.role != 'super_admin' and instance.uploader_id != self.request.user.id:
            raise PermissionDenied('只能编辑自己上传的文档')
        serializer.save()


@extend_schema(
    summary='删除知识文档',
    description='删除文档及其关联数据（OSS文件、MySQL记录）',
    tags=['知识库管理'],
    operation_id='删除知识文档',
)
class KnowledgeDocDeleteView(generics.DestroyAPIView):
    """
    知识文档删除接口

    DELETE /api/knowledge/docs/{id}/
    功能：删除文档及其关联数据——先删 OSS 文件，再事务内删 MySQL 记录（Keyword / DocChunk 级联清理）
    权限规则：
        - super_admin（超级管理员）: 可删除平台全部文档
        - admin（学院管理员）: 仅能删除自己上传的文档
        - 普通登录用户: 仅能删除自己上传的文档
    处理流程：
        1. IsOwnerOrReadOnly 权限校验（超管全放行，其他角色仅允许操作自己上传的文档）
        2. oss_client.delete(instance.oss_key) — 删除 OSS 文件（含容错处理）
        3. transaction.atomic() 内 instance.delete() — 删除 MySQL 记录
           CASCADE 自动级联：KnowledgeDoc → Keyword + DocChunk
        4. 返回 {"detail": "删除成功"}
    注意：
        - Milvus 向量暂不同步删除（TODO，甲同学完成后集成）
        - OSS 删除失败不阻塞 MySQL 删除（OSS 文件可后续清理）
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self._filter_deletable(KnowledgeDoc.objects.all(), self.request.user)

    def perform_destroy(self, instance):
        if instance.oss_key:
            deleted = oss_client.delete(instance.oss_key)
            if not deleted:
                logger.warning(f'OSS 文件删除失败 oss_key={instance.oss_key}，跳过')
        with transaction.atomic():
            instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': '删除成功'})

    @staticmethod
    def _filter_deletable(queryset, user):
        """按角色过滤可删除的文档（通过 college_id/department_id 判断归属）"""
        # 校级文档：college_id 和 department_id 均为 null
        SCHOOL_LEVEL = Q(college_id__isnull=True, department_id__isnull=True)

        if user.role == 'super_admin':
            return queryset
        if user.role in ('college_admin', 'admin'):
            return queryset.filter(
                SCHOOL_LEVEL
                | Q(college_id=user.college_id)
                | Q(uploader_id=user.id),
            )
        if user.role == 'dept_admin':
            return queryset.filter(
                SCHOOL_LEVEL
                | Q(department_id=user.department_id)
                | Q(uploader_id=user.id),
            )
        return queryset.none()


class KnowledgeDocBatchDeleteView(APIView):
    """
    批量删除知识文档

    DELETE /api/knowledge/docs/batch/
    功能：批量删除文档，逐条删 OSS + MySQL，统计成功/失败
    请求参数（JSON body）：
        - ids (list[int], required): 要删除的文档 ID 列表，最多 100 条
    权限规则：同 KnowledgeDocDeleteView
    返回：{"deleted": N, "failed": N, "total": N, "errors": [{id, detail}, ...]}
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        from .serializers import BatchDeleteSerializer
        serializer = BatchDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['ids']

        deletable_qs = KnowledgeDocDeleteView._filter_deletable(
            KnowledgeDoc.objects.filter(id__in=ids), request.user,
        )
        deletable = list(deletable_qs)
        deletable_ids = {d.id for d in deletable}

        deleted, failed, errors = 0, 0, []
        for doc_id in ids:
            if doc_id not in deletable_ids:
                failed += 1
                errors.append({'id': doc_id, 'detail': '无权限删除此文档'})
                continue
            try:
                doc = next(d for d in deletable if d.id == doc_id)
                if doc.oss_key:
                    oss_client.delete(doc.oss_key)
                with transaction.atomic():
                    doc.delete()
                deleted += 1
            except Exception as e:
                failed += 1
                errors.append({'id': doc_id, 'detail': str(e)})
                logger.error(f'批量删除失败 id={doc_id}: {e}')

        return Response({
            'deleted': deleted, 'failed': failed,
            'total': len(ids), 'errors': errors,
        })


@extend_schema(
    summary='下载知识文档',
    description='生成 OSS 签名下载 URL（有效期 300 秒）',
    tags=['知识库管理'],
    operation_id='下载知识文档',
)
class KnowledgeDocDownloadView(generics.RetrieveAPIView):
    """
    文档下载——生成 OSS 签名下载 URL

    GET /api/knowledge/docs/{id}/download/
    功能：为指定文档生成阿里云 OSS 签名下载 URL（有效期 300 秒），前端拿到后直接跳转或 <a> 下载
    权限规则：
        - super_admin（超级管理员）: 可下载平台全部文档的源文件
        - admin（学院管理员）: 仅能下载自己上传的文档
        - 普通登录用户: 仅能下载自己上传的文档
    处理流程：
        1. IsLoginUser 校验登录状态
        2. get_queryset 按角色限制可下载范围（超管全放行，其余仅限本人上传）
        3. oss_client.get_download_url(instance.oss_key) 生成签名 URL
    返回：{"download_url": "https://bucket.oss-cn-hangzhou.aliyuncs.com/...?Expires=...&Signature=..."}
    """
    permission_classes = [IsLoginUser]

    def get_queryset(self):
        user = self.request.user
        queryset = KnowledgeDoc.objects.all()
        return KnowledgeDocListView._admin_scope_filter(queryset, user)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        download_url = oss_client.get_download_url(
            instance.oss_key, file_name=instance.file_name,
        )
        return Response({'download_url': download_url})


@extend_schema(
    summary='关键词列表',
    description='返回指定文档的所有关键词，按权重降序排列',
    tags=['关键词管理'],
    operation_id='关键词列表',
)
class KeywordListView(APIView):
    """
    文档关键词列表

    GET /api/knowledge/docs/{doc_id}/keywords/
    功能：返回指定文档的所有关键词，按权重（weight）降序排列，所有登录用户均可查看
    返回：关键词数组，每条包含 id / phrase / match_type / weight / created_by_name / created_at
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, doc_id):
        """返回文档的关键词列表"""
        # 范围过滤：与文档列表一致，防止读取他人私密/未完成文档的关键词元数据
        doc = get_object_or_404(
            KnowledgeDocListView._admin_scope_filter(
                KnowledgeDoc.objects.all(), request.user,
            ),
            id=doc_id,
        )
        keywords = doc.keywords.order_by('-weight', '-created_at')
        return Response(KeywordSerializer(keywords, many=True).data)


@extend_schema(
    summary='添加关键词',
    description='为指定文档添加关键词，用于提升检索召回时的命中权重',
    tags=['关键词管理'],
    operation_id='添加关键词',
)
class KeywordCreateView(APIView):
    """
    为文档添加关键词

    POST /api/knowledge/docs/{doc_id}/keywords/
    功能：为指定文档添加一个关键词，用于提升检索召回时的命中权重
    请求参数：
        - phrase (str, required): 关键词或关键语句
        - match_type (str, default=fuzzy): 匹配类型（exact:精确匹配 / fuzzy:模糊匹配 / semantic:语义关联）
        - weight (int, default=1): 权重 1-10，数值越高检索时排名越靠前
    权限规则：
        - super_admin（超级管理员）: 可给任意文档添加关键词
        - admin（学院管理员）: 仅能给自己上传的文档添加关键词
    约束：同一文档内 phrase 唯一，重复添加返回 409 CONFLICT
    返回：
        - 201: {"id": <keyword.id>, "phrase": "...", "match_type": "...", "weight": N, "created_by_name": "...", "created_at": "..."}
        - 409: {"detail": "关键词 \"xxx\" 已存在于此文档中", "code": "CONFLICT"}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request, doc_id):
        """为指定文档创建关键词"""
        doc = get_object_or_404(KnowledgeDoc, id=doc_id)

        # 权限校验：超管全放行，其余仅允许操作自己上传的文档
        if request.user.role != 'super_admin' and doc.uploader_id != request.user.id:
            raise PermissionDenied('只能给自己上传的文档添加关键词')

        # 参数校验
        serializer = KeywordCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 同一文档内 phrase 唯一性校验
        if Keyword.objects.filter(doc=doc, phrase=data['phrase']).exists():
            return Response(
                {'detail': f'关键词 "{data["phrase"]}" 已存在于此文档中', 'code': ErrorCode.CONFLICT.value},
                status=status.HTTP_409_CONFLICT,
            )

        # 创建关键词
        keyword = Keyword.objects.create(
            doc=doc,
            phrase=data['phrase'],
            match_type=data.get('match_type', 'fuzzy'),
            weight=data.get('weight', 1),
            created_by=request.user,
        )

        return Response(KeywordSerializer(keyword).data, status=status.HTTP_201_CREATED)


@extend_schema(
    summary='编辑关键词',
    description='编辑指定关键词的匹配类型和权重',
    tags=['关键词管理'],
    operation_id='编辑关键词',
)
class KeywordUpdateView(APIView):
    """
    编辑关键词

    PUT /api/knowledge/keywords/{id}/
    功能：编辑指定关键词的匹配类型和权重
    请求参数（均可选，按需传入）：
        - phrase (str, optional): 关键词或关键语句
        - match_type (str, optional): 匹配类型（exact:精确匹配 / fuzzy:模糊匹配 / semantic:语义关联）
        - weight (int, optional): 权重 1-10，数值越高检索时排名越靠前
    权限规则：
        - super_admin（超级管理员）: 可编辑任意文档下的关键词
        - admin（学院管理员）: 仅能编辑自己上传的文档下的关键词
    返回：更新后的关键词对象
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def put(self, request, pk):
        """编辑指定关键词"""
        keyword = get_object_or_404(Keyword.objects.select_related('doc'), id=pk)

        # 权限校验：超管全放行，其余仅允许编辑自己文档下的关键词
        if request.user.role != 'super_admin' and keyword.doc.uploader_id != request.user.id:
            raise PermissionDenied('只能编辑自己上传的文档下的关键词')

        # 参数校验
        serializer = KeywordCreateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 更新字段（仅更新传入的字段）
        if 'phrase' in data:
            keyword.phrase = data['phrase']
        if 'match_type' in data:
            keyword.match_type = data['match_type']
        if 'weight' in data:
            keyword.weight = data['weight']
        keyword.save()

        return Response(KeywordSerializer(keyword).data)


@extend_schema(
    summary='删除关键词',
    description='删除指定关键词记录',
    tags=['关键词管理'],
    operation_id='删除关键词',
)
class KeywordDeleteView(APIView):
    """
    删除关键词

    DELETE /api/knowledge/keywords/{id}/
    功能：删除指定关键词记录
    权限规则：
        - super_admin（超级管理员）: 可删除任意文档下的关键词
        - admin（学院管理员）: 仅能删除自己上传的文档下的关键词
    返回：{"detail": "删除成功"}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def delete(self, request, pk):
        """删除指定关键词"""
        keyword = get_object_or_404(Keyword.objects.select_related('doc'), id=pk)

        # 权限校验：超管全放行，其余仅允许删除自己文档下的关键词
        if request.user.role != 'super_admin' and keyword.doc.uploader_id != request.user.id:
            raise PermissionDenied('只能删除自己上传的文档下的关键词')

        keyword.delete()
        return Response({'detail': '删除成功'})


@extend_schema(
    summary='获取上传凭证',
    description='校验文件 MD5 去重后，签发阿里云 OSS STS 临时凭证供前端直传',
    tags=['文件上传'],
    operation_id='获取上传凭证',
)
class UploadSTSView(APIView):
    """
    获取 OSS 直传 STS 凭证（含 MD5 去重检查）

    POST /api/knowledge/upload/sts/
    功能：校验文件 MD5 去重后，签发阿里云 OSS STS 临时凭证供前端直传
    参数：
        - file_name (str, required): 原始文件名
        - file_size (int, required): 文件大小（字节）
        - md5 (str, required): 文件 MD5 哈希值
    返回：
        - 重复文件: {"duplicate": true, "resource_id": <doc.id>}
        - 正常凭证: {"oss_key": "...", "upload_url": "...", "sts_credentials": {...}, "dev_mode": false, "duplicate": false}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        """处理 STS 凭证签发请求"""
        serializer = UploadSTSRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        md5_hash = serializer.validated_data['md5']
        file_name = serializer.validated_data['file_name']
        college_id = request.user.college_id or 0

        # MD5 去重检查
        duplicate_doc = oss_client.check_duplicate(md5_hash)
        if duplicate_doc:
            return Response({
                'duplicate': True,
                'resource_id': duplicate_doc.id,
            })

        # 获取 STS 临时凭证（scope 根据上传者角色自动确定）
        user = request.user
        if user.role == 'super_admin':
            scope = 'school'
            owner_id = 0
        elif user.role == 'dept_admin' and user.department_id:
            scope = 'department'
            owner_id = user.department_id
        else:
            scope = 'college'
            owner_id = user.college_id or 0
        credentials = oss_client.get_sts_credentials(file_name, scope, owner_id)
        return Response({
            'oss_key': credentials['oss_key'],
            'upload_url': credentials['upload_url'],
            'sts_credentials': credentials.get('sts_credentials'),
            'dev_mode': credentials.get('dev_mode', False),
            'duplicate': False,
        })


@extend_schema(
    summary='上传完成回调',
    description='OSS 前端直传完成后回调，创建知识文档记录',
    tags=['文件上传'],
    operation_id='上传完成回调',
)
class UploadCallbackView(APIView):
    """
    OSS 上传完成回调，创建知识文档记录

    POST /api/knowledge/upload/callback/
    功能：OSS 前端直传完成后回调，校验 oss_key 安全性、MD5 去重后创建知识文档记录
    参数：
        - oss_key (str, required): OSS 对象 Key
        - title (str, optional): 文档标题（未提供时 AI 生成）
        - description (str, optional): 文档描述（未提供时 AI 生成）
        - file_name (str, required): 原始文件名
        - file_size (int, required): 文件大小（字节）
        - file_type (str, required): 文件类型
        - md5 (str, required): 文件 MD5 哈希值
        - visibility (str, default=public): 可见性（public/private）
        - college_id (int, optional): 所属学院 ID（默认取上传者学院）
        - discipline_id (int, optional): 学科方向 ID
    返回：
        - 新建成功: {"id": <doc.id>, "status": "uploaded"}
        - 重复文件: {"id": <existing_doc.id>, "status": "<status>", "duplicate": true}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        """处理上传完成回调，创建文档记录"""
        serializer = UploadCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 校验 oss_key 格式合法性（防止路径穿越攻击）
        self._validate_oss_key(data['oss_key'])

        # 校验分类关联数据合法性
        self._validate_classification(data)

        # 再次 MD5 去重（防止并发上传导致重复）
        # 仅当已存在文档对当前上传者可见时才提示重复，避免泄露他人文档存在性
        duplicate_doc = oss_client.check_duplicate(data['md5'])
        if duplicate_doc and (
            request.user.role == 'super_admin'
            or duplicate_doc.uploader_id == request.user.id
            or duplicate_doc.visibility == 'public'
        ):
            return Response({
                'id': duplicate_doc.id,
                'status': duplicate_doc.status,
                'duplicate': True,
            })

        # 创建文档记录
        # 文档归属根据上传者角色自动确定：非超管强制使用本人归属，防止跨学院/跨部门污染
        if request.user.role == 'super_admin':
            doc_college_id = data.get('college_id') or None
            doc_department_id = data.get('department_id') or None
        else:
            doc_college_id = request.user.college_id
            doc_department_id = request.user.department_id

        new_doc = KnowledgeDoc.objects.create(
            oss_key=data['oss_key'],
            title=data['title'],
            description=data.get('description', ''),
            file_name=data['file_name'],
            file_size=data['file_size'],
            file_type=normalize_file_type(data['file_type']),
            md5_hash=data['md5'],
            source_type='upload',
            uploader=request.user,
            status='uploaded',
            college_id=doc_college_id,
            department_id=doc_department_id,
            discipline_id=data.get('discipline_id'),
            visibility=data.get('visibility', 'public'),
        )

        # 创建关联关键词
        keywords = data.get('keywords', [])
        for phrase in keywords:
            Keyword.objects.create(
                doc=new_doc,
                phrase=phrase,
                match_type='fuzzy',
                weight=1,
                created_by=request.user,
            )

        # TODO: 调用 Celery 异步索引任务（甲同学完成后集成）
        # index_document.delay(doc_id=new_doc.id)

        # 自动触发 AI 日期提取（异步，不阻塞上传主链路）
        # 遵循 CONVENTIONS.md 第 9 节：异常处理下沉到 utils 层，视图层不写 try/except
        from utils.celery_dispatcher import safe_delay
        from .tasks import extract_freshness_task, index_document_task
        safe_delay(extract_freshness_task, new_doc.id)
        safe_delay(index_document_task, new_doc.id)

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
        })

    @staticmethod
    def _validate_classification(data: dict) -> None:
        """校验关联数据合法性（college_id / department_id 存在性）"""
        if data.get('college_id') and not College.objects.filter(id=data['college_id']).exists():
            raise ValidationError({'college_id': '学院不存在'})
        if data.get('department_id') and not Department.objects.filter(id=data['department_id']).exists():
            raise ValidationError({'department_id': '部门不存在'})

    @staticmethod
    def _validate_oss_key(oss_key: str) -> None:
        """校验 oss_key 格式合法性，防止路径穿越攻击"""
        if '..' in oss_key:
            raise ValidationError({'oss_key': '包含非法路径字符 ".."'})
        if oss_key.startswith('/'):
            raise ValidationError({'oss_key': '不能以 "/" 开头'})
        if not oss_key.startswith('knowledge/'):
            raise ValidationError({'oss_key': 'OSS Key 格式不合法，必须以 "knowledge/" 开头'})



class TextUploadView(APIView):
    """
    文本录入接口

    POST /api/knowledge/upload/text/
    功能：用户输入文本内容，后端直传 OSS 并创建知识文档记录。
          visibility 由用户选择，文档归属根据上传者角色自动确定；title/description/keywords 未提供时由 AI 自动填充。
    请求参数：
        - content (str, required): 文本内容
        - visibility (str, default=public): 可见性（public/private）
        - title (str, optional): 文档标题（未提供时 AI 自动生成）
        - description (str, optional): 文档描述（未提供时 AI 生成）
        - college_id (int, optional): 学院 ID（默认使用上传者学院ID）
        - department_id (int, optional): 部门 ID（默认使用上传者部门ID）
        - discipline_id (int, optional): 学科方向 ID
        - keywords (list[str], optional): 关键词列表（未提供时 AI 提取）
    返回：{"id": <doc.id>, "status": "uploaded", "file_name": "<title>.txt"}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [CleanJSONParser]

    def post(self, request):
        """处理文本录入请求"""
        import hashlib
        import uuid

        # 填充默认值：学院ID和部门ID默认使用上传者的
        req_data = request.data.copy()
        if 'college_id' not in req_data or req_data.get('college_id') in (None, '', 'null'):
            req_data['college_id'] = request.user.college_id
        if 'department_id' not in req_data or req_data.get('department_id') in (None, '', 'null'):
            req_data['department_id'] = request.user.department_id

        serializer = TextUploadSerializer(data=req_data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        content = data['content']

        # 校验分类关联数据合法性（复用 UploadCallbackView 的校验逻辑）
        UploadCallbackView._validate_classification(data)

        # 判断用户是否显式传了 visibility（检查原始请求，排除序列化器默认值）
        user_provided_visibility = 'visibility' in request.data

        # AI 自动填充缺失字段
        user_title = data.get('title', '')
        user_description = data.get('description', '')
        user_keywords = data.get('keywords', [])

        needs_ai = (
            not user_title
            or not user_description
            or not user_keywords
            or not user_provided_visibility
        )
        if needs_ai:
            ai_result = _ai_classify(user_title or '', content[:2000])
            if ai_result:
                title = user_title or ai_result['title'] or '未命名文档'
                description = user_description or ai_result['description'] or ''
                keywords = user_keywords or ai_result['keywords']
                visibility = data['visibility'] if user_provided_visibility else ai_result['visibility']
            else:
                # AI 不可用降级
                title = user_title or '未命名文档'
                description = user_description
                keywords = user_keywords or []
                visibility = data['visibility']
        else:
            title = user_title
            description = user_description
            keywords = user_keywords
            visibility = data['visibility']

        title = title or '未命名文档'
        description = description or ''

        # 生成 OSS 对象 Key（根据上传者角色确定路径）
        now = datetime.now()
        if request.user.role == 'super_admin':
            oss_scope = 'school'
            owner_id = 0
        elif request.user.role == 'dept_admin' and request.user.department_id:
            oss_scope = 'department'
            owner_id = request.user.department_id
        else:
            oss_scope = 'college'
            owner_id = request.user.college_id or 0
        oss_key = f'knowledge/{oss_scope}/{owner_id}/{now:%Y-%m}/{uuid.uuid4().hex[:12]}.txt'

        # 上传文本到 OSS
        success = oss_client.upload_text(content, oss_key)
        if not success:
            return Response(
                {'detail': '文本上传到 OSS 失败，请稍后重试', 'code': ErrorCode.UPLOAD_FAILED.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 计算 MD5
        md5_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

        # 生成 OSS 访问 URL
        oss_url = f'https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}'

        # 文档归属根据上传者角色自动确定
        if request.user.role == 'super_admin':
            doc_college_id = None
            doc_department_id = None
        else:
            doc_college_id = data.get('college_id') or request.user.college_id
            doc_department_id = data.get('department_id') or request.user.department_id

        # 创建 KnowledgeDoc 记录
        new_doc = KnowledgeDoc.objects.create(
            title=title,
            description=description,
            source_type='upload',
            oss_key=oss_key,
            oss_url=oss_url,
            file_name=f'{title}.txt',
            file_type='txt',
            file_size=len(content.encode('utf-8')),
            md5_hash=md5_hash,
            college_id=doc_college_id,
            department_id=doc_department_id,
            discipline_id=data.get('discipline_id'),
            uploader=request.user,
            status='uploaded',
            visibility=visibility,
        )

        # 创建关联关键词
        for phrase in keywords:
            if isinstance(phrase, str) and phrase.strip():
                Keyword.objects.create(
                    doc=new_doc,
                    phrase=phrase.strip(),
                    match_type='fuzzy',
                    weight=1,
                    created_by=request.user,
                )

        # 触发异步索引（切片 + 向量化），使文档可被 AI 智能问答检索
        from utils.celery_dispatcher import safe_delay
        from .tasks import extract_freshness_task, index_document_task
        safe_delay(extract_freshness_task, new_doc.id)
        safe_delay(index_document_task, new_doc.id)

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
            'file_name': new_doc.file_name,
            'title': new_doc.title,
            'description': new_doc.description,
            'keywords': keywords,
        })


class KnowledgeDocPreviewView(APIView):
    """
    文档内容预览接口

    GET /api/knowledge/docs/{id}/preview/
    功能：根据文件类型返回预览内容——文本类返回原文，音视频/图片/文档返回 OSS 签名预览 URL
    权限规则：
        - super_admin：可预览平台全部文档
        - admin / college_admin / dept_admin：按对应角色权限预览
        - user（普通学生）：不能直接预览单条文档，请通过 /api/chat/ask/ AI 检索
    返回格式：
        - 文本类: {"preview_type": "text", "content": "<原文>", "file_type": "txt", "file_name": "..."}
        - 音频类: {"preview_type": "url", "content": "<signed_url>", "file_type": "audio", "file_name": "..."}
        - 视频类: {"preview_type": "url", "content": "<signed_url>", "file_type": "video", "file_name": "..."}
        - 图片类: {"preview_type": "url", "content": "<signed_url>", "file_type": "image", "file_name": "..."}
        - 文档类: {"preview_type": "url", "content": "<signed_url>", "file_type": "document", "file_name": "..."}
    """
    permission_classes = [permissions.IsAuthenticated]

    # 音频扩展名
    AUDIO_TYPES = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'}
    # 视频扩展名
    VIDEO_TYPES = {'mp4', 'webm', 'mov', 'avi', 'mkv', 'flv'}
    # 图片扩展名
    IMAGE_TYPES = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'}
    # 纯文本扩展名（返回原文）
    TEXT_TYPES = {'txt', 'md', 'html', 'csv', 'json', 'xml', 'yaml', 'yml'}

    def get(self, request, pk):
        """返回文档预览内容"""
        # 范围过滤：user 仅可预览本人上传；管理员仅限自己管理范围内文档，
        # 防止通过枚举 pk 预览其他学院/部门/私密文档
        doc = get_object_or_404(
            KnowledgeDocListView._admin_scope_filter(
                KnowledgeDoc.objects.select_related('uploader'), request.user,
            ),
            id=pk,
        )
        ft = doc.file_type.lower() if doc.file_type else 'other'

        # 纯文本：返回原文（从 OSS 拉取）
        if ft in self.TEXT_TYPES:
            return self._text_preview(doc, ft)

        # 音频：返回签名预览 URL（前端 <audio> 播放）
        if ft in self.AUDIO_TYPES:
            return self._media_preview(doc, 'audio')

        # 视频：返回签名预览 URL（前端 <video> 播放）
        if ft in self.VIDEO_TYPES:
            return self._media_preview(doc, 'video')

        # 图片：返回签名预览 URL（前端 <img> 渲染）
        if ft in self.IMAGE_TYPES:
            return self._media_preview(doc, 'image')

        # 文档 / 其他：返回签名预览 URL
        return self._media_preview(doc, 'document')

    def _media_preview(self, doc, file_type):
        """生成签名预览 URL，供前端 <audio>/<video>/<img> 等标签使用"""
        preview_url = oss_client.get_preview_url(doc.oss_key)
        return Response({
            'preview_type': 'url',
            'content': preview_url,
            'file_type': file_type,
            'file_name': doc.file_name,
        })

    def _text_preview(self, doc, file_type):
        """从 OSS 下载文本内容并直接返回"""
        from utils.http_client import ExternalClient

        download_url = oss_client.get_download_url(doc.oss_key, expires=60)
        try:
            resp = ExternalClient.get('oss_download', download_url)
            resp.raise_for_status()
            text_content = resp.text
        except Exception as e:
            logger.error(f'预览文本下载失败 oss_key={doc.oss_key}: {e}')
            text_content = ''
        return Response({
            'preview_type': 'text',
            'content': text_content,
            'file_type': file_type,
            'file_name': doc.file_name,
        })
@extend_schema(
    summary='AI 提取发布日期',
    description='调用 LLM 从文档内容中提取发布日期 / 适用学期，写回 freshness_date',
    tags=['文件上传'],
    operation_id='AI提取发布日期',
)
class ExtractFreshnessView(APIView):
    """
    AI 提取发布日期

    POST /api/knowledge/docs/{id}/extract-freshness/
    功能：
        1. 从 OSS 下载指定文档
        2. 按文件类型抽取文本（PDF/DOCX/PPTX/XLSX/MD/TXT/HTML）
        3. 调用 DeepSeek 提取"发布日期 / 适用学期"
        4. 把结果写回 KnowledgeDoc.freshness_date
    参数：
        - id (path int, required): KnowledgeDoc 主键
        - async (query bool, default=true): true=异步（Celery）；false=同步阻塞（限调试）
    权限：
        - super_admin: 可对任意文档触发
        - admin: 仅可对自己上传的文档触发
    返回：
        - 202 Accepted: {"task_id": "...", "status": "queued", "doc_id": <int>}
          （Celery 可用时返回；celery 入队失败/未安装时自动降级为同步执行，返回 200）
        - 200 OK: 单个对象 {freshness_date, confidence, source_excerpt, raw_response}
          （async=false 显式同步 OR Celery 不可用自动降级）
    错误码：
        - 401 UNAUTHORIZED: 未登录
        - 403 PERMISSION_DENIED: 越权触发
        - 404 NOT_FOUND: 文档不存在
    注意：
        - 图片/视频/音频类型无法抽取文本，confidence='none'
        - 失败时 freshness_date 留空，前端可再次手动触发
        - celery 未安装（本地开发）时 utils.shared_task_compat 会自动同步执行任务，
          接口仍返回 200 而非 500；部署到生产前请确保 requirements.txt 中的 celery 已安装
          且 worker 正常运行
    """
    permission_classes = [IsLoginUser]

    def post(self, request, pk):
        doc = get_object_or_404(KnowledgeDoc, pk=pk)

        # 权限校验：超管全放行，admin 仅限自己上传的文档
        if request.user.role not in ('super_admin', 'admin'):
            self._raise_permission_error('只有管理员可以触发 AI 日期提取')
        if request.user.role == 'admin' and doc.uploader_id != request.user.id:
            self._raise_permission_error('只能对自己上传的文档触发 AI 日期提取')

        # 是否走异步
        run_async = request.query_params.get('async', 'true').lower() != 'false'
        if run_async:
            from .tasks import extract_freshness_task
            from utils.celery_dispatcher import safe_delay
            # safe_delay 返回 AsyncResult → Celery 入队成功；返回 None → Celery 不可用
            async_result = safe_delay(extract_freshness_task, doc.id)
            if async_result is not None:
                return Response(
                    {'task_id': async_result.id, 'status': 'queued', 'doc_id': doc.id},
                    status=status.HTTP_202_ACCEPTED,
                )
            # Celery 不可用 → 降级到同步执行

        # 同步执行（async=false 显式 OR Celery 不可用降级）
        result = extract_freshness_for_doc(doc.id)
        if result is None:
            return Response(
                {'detail': '文档不存在', 'code': ErrorCode.NOT_FOUND.value},
                status=status.HTTP_404_NOT_FOUND,
            )
        # 200 返回单个对象（按 CONVENTIONS.md 第 8 节："返回单个对象"）
        return Response({
            'freshness_date': result.freshness_date,
            'confidence': result.confidence,
            'source_excerpt': result.source_excerpt,
            'raw_response': result.raw_response,
        })


class FileUploadView(APIView):
    """
    文件直传上传接口（multipart/form-data）

    POST /api/knowledge/upload/file/
    功能：接收前端上传的文件，后端存到 OSS 并创建知识文档记录。
          visibility 由用户选择，文档归属根据上传者角色自动确定；title/description/keywords 未提供时由 AI 自动填充。
    请求参数（form-data）：
        - file (file, required): 上传的文件
        - visibility (str, default=public): 可见性（public/private）
        - title (str, optional): 文档标题（默认使用文件名，AI 可优化）
        - description (str, optional): 文档描述（未提供时 AI 生成）
        - college_id (int, optional): 学院 ID（默认使用上传者学院ID）
        - department_id (int, optional): 部门 ID（默认使用上传者部门ID）
        - discipline_id (int, optional): 学科方向 ID
        - keywords (str, optional): JSON 数组字符串，如 '["关键词1","关键词2"]'（未提供时 AI 提取）
    返回：{"id": <doc.id>, "status": "uploaded", "file_name": "..."}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    # 可提取文本的文件类型
    TEXT_EXTRACTABLE_TYPES = {'txt', 'md', 'html', 'csv', 'json', 'xml', 'yaml', 'yml'}
    # 可用解析器提取文本的文档类型
    DOCUMENT_EXTRACTABLE_TYPES = {'pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx'}

    def post(self, request):
        """处理文件上传"""
        import hashlib
        import json
        import os
        import tempfile
        import uuid

        # 填充默认值：学院ID和部门ID默认使用上传者的
        req_data = request.data.copy()
        if 'college_id' not in req_data or req_data.get('college_id') in (None, '', 'null'):
            req_data['college_id'] = request.user.college_id
        if 'department_id' not in req_data or req_data.get('department_id') in (None, '', 'null'):
            req_data['department_id'] = request.user.department_id

        serializer = FileUploadSerializer(data=req_data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        uploaded_file = data['file']

        # 校验分类关联数据合法性
        UploadCallbackView._validate_classification(data)

        # 校验文件类型
        ext = uploaded_file.name.rsplit('.', 1)[-1].lower() if '.' in uploaded_file.name else 'other'
        if ext not in ALLOWED_FILE_TYPES:
            raise ValidationError(
                {'file': f'不支持的文件类型 ".{ext}"，支持的类型：{", ".join(sorted(ALLOWED_FILE_TYPES))}'}
            )

        # 校验文件大小
        max_size = FILE_SIZE_LIMITS.get(ext, FILE_SIZE_LIMITS['other'])
        if uploaded_file.size > max_size:
            max_mb = max_size // (1024 * 1024)
            cur_mb = uploaded_file.size // (1024 * 1024)
            raise ValidationError(
                {'file': f'{ext} 文件大小不能超过 {max_mb}MB，当前大小 {cur_mb}MB'}
            )

        # 生成 OSS 对象 Key（根据上传者角色确定路径）
        now = datetime.now()
        safe_filename = f'{uuid.uuid4().hex[:12]}.{ext}'
        if request.user.role == 'super_admin':
            oss_scope = 'school'
            owner_id = 0
        elif request.user.role == 'dept_admin' and request.user.department_id:
            oss_scope = 'department'
            owner_id = request.user.department_id
        else:
            oss_scope = 'college'
            owner_id = request.user.college_id or 0
        oss_key = f'knowledge/{oss_scope}/{owner_id}/{now:%Y-%m}/{safe_filename}'

        # 读取文件二进制内容 + 计算 MD5
        file_bytes = uploaded_file.read()
        md5_hash = hashlib.md5(file_bytes).hexdigest()

        # 上传到 OSS
        try:
            if not oss_client.bucket:
                logger.error(f'OSS Bucket 未初始化，请检查 OSS 配置')
                return Response(
                    {'detail': 'OSS 配置异常，请联系管理员', 'code': ErrorCode.OSS_CONFIG_ERROR.value},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            oss_client.bucket.put_object(oss_key, file_bytes)
            oss_url = f'https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}'
            logger.info(f'OSS 上传成功 oss_key={oss_key}, size={len(file_bytes)}')
        except oss2.exceptions.OssError as e:
            logger.error(f'OSS 服务端错误 oss_key={oss_key}: {e}')
            return Response(
                {'detail': f'OSS 服务错误: {str(e)}', 'code': ErrorCode.OSS_SERVER_ERROR.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            logger.error(f'OSS 文件上传失败 oss_key={oss_key}: {type(e).__name__}: {e}')
            return Response(
                {'detail': f'文件上传失败: {str(e)}', 'code': ErrorCode.UPLOAD_FAILED.value},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 确定初始 title：优先用户提供 → 文件名（去扩展名）
        user_title = data.get('title', '')
        fallback_title = uploaded_file.name.rsplit('.', 1)[0] if '.' in uploaded_file.name else uploaded_file.name
        title = user_title or fallback_title

        # 提取文本内容（用于 AI 分析）
        extracted_text = ''
        if ext in self.TEXT_EXTRACTABLE_TYPES:
            try:
                extracted_text = file_bytes.decode('utf-8', errors='replace')[:4000]
            except Exception:
                pass
        elif ext in self.DOCUMENT_EXTRACTABLE_TYPES:
            try:
                from apps.knowledge.extractor import _EXTRACTORS
                parser = _EXTRACTORS.get(ext)
                if parser:
                    fd, tmp_path = tempfile.mkstemp(suffix=f'.{ext}')
                    os.close(fd)
                    try:
                        with open(tmp_path, 'wb') as f:
                            f.write(file_bytes)
                        parsed = parser(tmp_path)
                        if parsed:
                            extracted_text = parsed[:4000]
                    except Exception as e:
                        logger.warning(f'文档文本提取失败 ({ext}): {e}')
                    finally:
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
            except ImportError:
                logger.warning('无法导入文档解析器，跳过文档文本提取')

        # 判断用户是否显式传了 visibility（检查原始请求，排除序列化器默认值）
        user_provided_visibility = 'visibility' in request.data

        # AI 自动填充缺失字段
        user_description = data.get('description', '')
        # multipart 兼容：前端可能以 JSON 字符串 / 重复字段 / keywords[] 等多种形式发送关键词
        raw_keywords = _normalize_keywords(request, data.get('keywords', []))

        needs_ai = (
            not user_title
            or not user_description
            or not raw_keywords  # 直接检查列表是否为空
            or not user_provided_visibility
        )
        if needs_ai:
            ai_result = _ai_classify(title, extracted_text)
            if ai_result:
                title = user_title or ai_result['title'] or fallback_title
                description = user_description or ai_result['description'] or ''
                visibility = data['visibility'] if user_provided_visibility else ai_result['visibility']
                # 关键词：用户提供的优先
                if raw_keywords:
                    pass  # 用户已提供关键词，保留
                else:
                    raw_keywords = ai_result['keywords']
            else:
                # AI 不可用降级
                title = title or fallback_title
                description = user_description
                visibility = data['visibility']
        else:
            description = user_description
            visibility = data['visibility']

        # 二次归一：确保变量有值
        title = title or fallback_title
        description = description or ''

        # 文档归属根据上传者角色自动确定
        if request.user.role == 'super_admin':
            doc_college_id = None
            doc_department_id = None
        else:
            doc_college_id = data.get('college_id') or request.user.college_id
            doc_department_id = data.get('department_id') or request.user.department_id

        # 创建 KnowledgeDoc 记录
        new_doc = KnowledgeDoc.objects.create(
            title=title,
            description=description,
            source_type='upload',
            oss_key=oss_key,
            oss_url=oss_url,
            file_name=uploaded_file.name,
            file_type=normalize_file_type(ext),
            file_size=uploaded_file.size,
            md5_hash=md5_hash,
            college_id=doc_college_id,
            department_id=doc_department_id,
            discipline_id=data.get('discipline_id'),
            uploader=request.user,
            status='uploaded',
            visibility=visibility,
        )

        # 创建上传日志记录
        try:
            UploadLog.objects.create(
                user=request.user,
                file_name=uploaded_file.name,
                file_size=uploaded_file.size,
                file_type=ext,
                college_id=data.get('college_id') or request.user.college_id,
                department_id=data.get('department_id') or request.user.department_id,
                status='success',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:512],
            )
        except Exception as e:
            logger.error(f'创建上传日志失败: {e}')

        # 创建关联关键词（ListField 已保证 raw_keywords 为列表）
        for phrase in raw_keywords:
            if isinstance(phrase, str) and phrase.strip():
                Keyword.objects.create(
                    doc=new_doc, phrase=phrase.strip(),
                    match_type='fuzzy', weight=1, created_by=request.user,
                )

        # 触发异步索引（切片 + 向量化），使文档可被 AI 智能问答检索
        # 遵循规范第 9 节：异常处理下沉到 utils 层，视图层不写 try/except
        from utils.celery_dispatcher import safe_delay
        from .tasks import extract_freshness_task, index_document_task
        safe_delay(extract_freshness_task, new_doc.id)
        safe_delay(index_document_task, new_doc.id)

        # 确保 raw_keywords 始终是列表（给前端返回用）
        if not isinstance(raw_keywords, list):
            raw_keywords = []

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
            'file_name': new_doc.file_name,
            'title': new_doc.title,
            'description': new_doc.description,
            'keywords': raw_keywords,
        })


class BatchFileUploadView(APIView):
    """
    多文件批量上传接口（multipart/form-data）

    POST /api/knowledge/upload/batch/
    功能：一次性上传多个文件，每个文件独立调用 AI 自动生成标题/描述/关键词，
          文档归属根据上传者角色自动确定。
    请求参数（form-data）：
        - files (file[], required): 上传的文件列表（支持拖拽多选）
        - visibility (str, default=public): 所有文件的统一可见性（public/private）
    权限规则：IsAdminOrSuperAdmin，仅管理员及以上可上传
    返回：
        {
            "success": [{id, file_name, status, title, description, visibility, file_type, file_size}, ...],
            "failed": [{file_name, error}, ...],
            "total_count": N,
            "success_count": N,
            "failed_count": N
        }
    注意：
        - 单个文件失败不影响其他文件处理
        - 每个文件独立调用 AI，结果不会搞混
        - 返回顺序与上传顺序一致
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    # 可提取文本的文件类型
    TEXT_EXTRACTABLE_TYPES = {'txt', 'md', 'html', 'csv', 'json', 'xml', 'yaml', 'yml'}

    def post(self, request):
        """处理多文件批量上传"""
        import hashlib
        import json
        import uuid

        # 1. 获取文件列表
        files = request.FILES.getlist('files')
        if not files:
            return Response(
                {'detail': '请至少上传一个文件', 'code': ErrorCode.VALIDATION_ERROR.value},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 2. 获取统一的可见性设置
        visibility = request.data.get('visibility', 'public')
        if visibility not in ('public', 'private'):
            visibility = 'public'

        # 3. 根据上传者角色确定文档归属
        user = request.user
        if user.role == 'super_admin':
            doc_college_id = None
            doc_department_id = None
            oss_scope = 'school'
            owner_id = 0
        elif user.role == 'dept_admin' and user.department_id:
            doc_college_id = user.college_id
            doc_department_id = user.department_id
            oss_scope = 'department'
            owner_id = user.department_id
        else:
            doc_college_id = user.college_id
            doc_department_id = user.department_id
            oss_scope = 'college'
            owner_id = user.college_id or 0

        # 4. 逐个处理文件
        success_results = []
        failed_results = []

        for uploaded_file in files:
            file_name = uploaded_file.name
            try:
                # 4a. 校验文件类型
                ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else 'other'
                if ext not in ALLOWED_FILE_TYPES:
                    failed_results.append({
                        'file_name': file_name,
                        'error': f'不支持的文件类型 ".{ext}"',
                    })
                    continue

                # 4a2. 校验文件大小
                max_size = FILE_SIZE_LIMITS.get(ext, FILE_SIZE_LIMITS['other'])
                if uploaded_file.size > max_size:
                    max_mb = max_size // (1024 * 1024)
                    cur_mb = uploaded_file.size // (1024 * 1024)
                    failed_results.append({
                        'file_name': file_name,
                        'error': f'{ext} 文件大小不能超过 {max_mb}MB，当前大小 {cur_mb}MB',
                    })
                    continue

                # 4b. 生成 OSS 对象 Key 并上传
                now = datetime.now()
                safe_filename = f'{uuid.uuid4().hex[:12]}.{ext}'
                oss_key = f'knowledge/{oss_scope}/{owner_id}/{now:%Y-%m}/{safe_filename}'

                # 读取文件二进制内容 + 计算 MD5
                file_bytes = uploaded_file.read()
                md5_hash = hashlib.md5(file_bytes).hexdigest()

                # 上传到 OSS
                if not oss_client.bucket:
                    logger.error('OSS Bucket 未初始化，批量上传中止')
                    return Response(
                        {'detail': 'OSS 配置异常，请联系管理员', 'code': ErrorCode.OSS_CONFIG_ERROR.value},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                try:
                    oss_client.bucket.put_object(oss_key, file_bytes)
                    oss_url = f'https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}'
                    logger.info(f'批量上传 OSS 成功 oss_key={oss_key}, size={len(file_bytes)}')
                except oss2.exceptions.OssError as e:
                    logger.error(f'OSS 服务端错误 oss_key={oss_key}: {e}')
                    failed_results.append({
                        'file_name': file_name,
                        'error': f'OSS 服务错误: {str(e)}',
                    })
                    continue

                # 4c. 提取文本内容用于 AI 分析
                extracted_text = ''
                if ext in self.TEXT_EXTRACTABLE_TYPES:
                    try:
                        extracted_text = file_bytes.decode('utf-8', errors='replace')[:4000]
                    except Exception:
                        pass

                # 4d. AI 自动生成标题、描述、关键词
                fallback_title = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name
                ai_result = _ai_classify(fallback_title, extracted_text)
                if ai_result:
                    title = ai_result.get('title') or fallback_title
                    description = ai_result.get('description') or ''
                    raw_keywords = ai_result.get('keywords', [])
                else:
                    # AI 不可用降级：使用文件名作为标题
                    title = fallback_title
                    description = ''
                    raw_keywords = []

                # 4e. 创建 KnowledgeDoc 记录
                new_doc = KnowledgeDoc.objects.create(
                    title=title,
                    description=description,
                    source_type='upload',
                    oss_key=oss_key,
                    oss_url=oss_url,
                    file_name=file_name,
                    file_type=normalize_file_type(ext),
                    file_size=uploaded_file.size,
                    md5_hash=md5_hash,
                    college_id=doc_college_id,
                    department_id=doc_department_id,
                    uploader=user,
                    status='uploaded',
                    visibility=visibility,
                )

                # 4f. 创建关键词
                for phrase in raw_keywords:
                    if isinstance(phrase, str) and phrase.strip():
                        Keyword.objects.create(
                            doc=new_doc, phrase=phrase.strip(),
                            match_type='fuzzy', weight=1, created_by=user,
                        )

                # 4g. 创建上传日志
                try:
                    UploadLog.objects.create(
                        user=user,
                        file_name=file_name,
                        file_size=uploaded_file.size,
                        file_type=ext,
                        college_id=doc_college_id,
                        department_id=doc_department_id,
                        status='success',
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')[:512],
                    )
                except Exception as e:
                    logger.error(f'批量上传创建日志失败 file={file_name}: {e}')

                # 4g-2. 触发异步索引（切片 + 向量化），使文档可被 AI 智能问答检索
                from utils.celery_dispatcher import safe_delay
                from .tasks import extract_freshness_task, index_document_task
                safe_delay(extract_freshness_task, new_doc.id)
                safe_delay(index_document_task, new_doc.id)

                # 4h. 记录成功
                success_results.append({
                    'id': new_doc.id,
                    'file_name': new_doc.file_name,
                    'status': new_doc.status,
                    'title': new_doc.title,
                    'description': description,
                    'visibility': new_doc.visibility,
                    'file_type': new_doc.file_type,
                    'file_size': new_doc.file_size,
                })

            except Exception as e:
                logger.error(f'批量上传文件处理失败 file={file_name}: {type(e).__name__}: {e}')
                failed_results.append({
                    'file_name': file_name,
                    'error': str(e),
                })

        # 5. 返回汇总结果
        return Response({
            'success': success_results,
            'failed': failed_results,
            'total_count': len(files),
            'success_count': len(success_results),
            'failed_count': len(failed_results),
        })


def _normalize_keywords(request, validated_value) -> list:
    """稳健提取关键词列表，兼容 JSON body 与 multipart 的多种发送格式。

    multipart/form-data 上传文件时，前端可能以下列任意形式发送关键词：
        - 真数组（JSON body 或重复字段 keywords=a&keywords=b）
        - 单个 JSON 字符串，如 keywords='["关键词1","关键词2"]'
        - 数组命名字段 keywords[]=a&keywords[]=b

    统一归整为「去空后的字符串列表」，避免因格式不符被误判为空而触发 AI 重跑。

    Args:
        request: DRF Request，用于在 multipart 下回读原始字段
        validated_value: 序列化器 ListField 解析出的值（list 或其它）

    Returns:
        规整后的关键词字符串列表（可能为空）
    """
    import json

    candidates = []
    if isinstance(validated_value, list):
        candidates = list(validated_value)
    elif isinstance(validated_value, str) and validated_value.strip():
        candidates = [validated_value]

    # multipart 下若序列化器没接到，回读原始请求（兼容 keywords / keywords[] 两种命名）
    if not candidates and hasattr(request.data, 'getlist'):
        candidates = request.data.getlist('keywords') or request.data.getlist('keywords[]')

    # 单元素且是 JSON 数组字符串时展开，如 ['["a","b"]'] → ['a', 'b']
    if len(candidates) == 1 and isinstance(candidates[0], str):
        text = candidates[0].strip()
        if text.startswith('[') and text.endswith(']'):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    candidates = parsed
            except (json.JSONDecodeError, TypeError):
                pass

    # 归整为非空字符串列表
    return [str(k).strip() for k in candidates if str(k).strip()]


def _ai_classify(title: str = '', content: str = '') -> Optional[dict]:
    """调用 LLM 智能分析文档，返回 title/description/keywords/visibility。

    供 AIClassifyView、FileUploadView、TextUploadView 复用。
    失败时返回 None，调用方自行降级处理。

    Args:
        title: 文档标题（可为空字符串）
        content: 文本内容片段（可为空字符串）

    Returns:
        成功: {'title': str, 'description': str, 'keywords': list[str],
                'visibility': str, 'confidence': str}
        失败: None
    """
    import json
    import re

    prompt = (
        f"你是校园知识库智能助手。请根据文档信息完成以下任务：\n"
        f"1. 如果标题为空，根据内容生成一个简洁的文档标题（20字以内）\n"
        f"2. 如果内容非空，生成一段100-200字的文档描述\n"
        f"3. 提取5-10个关键词\n"
        f"4. 推荐可见性（visibility）：\n"
        f"   - public: 公开（一般文档、教学资料等）\n"
        f"   - private: 私密（涉及人事、财务、敏感信息等）\n\n"
        f"文档标题：{title or '（未提供，请根据内容生成）'}\n"
        f"文档内容：{content[:2000] or '（未提供）'}\n\n"
        f"只返回JSON（不要markdown代码块标记）：\n"
        f'{{"title":"生成的标题","description":"生成的描述","keywords":["关键词1","关键词2",...],"visibility":"public|private","confidence":"high|medium|low"}}'
    )

    try:
        from utils.llm import llm_client
        raw = llm_client.chat(
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.0, max_tokens=500,
        )
    except Exception as e:
        logger.error(f'LLM 分析失败: {e}')
        return None

    if not raw:
        logger.warning('LLM 分析返回空结果')
        return None

    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group(0)) if match else json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        logger.warning(f'LLM 分析解析失败 raw={raw[:200]}')
        return None

    # 校验 AI 返回的 visibility 是否在合法范围内
    ai_visibility = result.get('visibility', 'public')
    if ai_visibility not in ('public', 'private'):
        ai_visibility = 'public'

    return {
        'title': result.get('title', '') or title or '未命名文档',
        'description': result.get('description', ''),
        'keywords': result.get('keywords', []),
        'visibility': ai_visibility,
        'confidence': result.get('confidence', 'medium'),
    }


class AIClassifyView(APIView):
    """
    AI 智能分析推荐

    POST /api/knowledge/upload/ai-classify/
    功能：接收文件标题和内容片段，调用 LLM 分析并返回推荐的标题、描述和关键词
    请求参数：
        - title (str, optional): 文件标题（为空时 AI 根据内容自动生成）
        - content (str, optional): 文本内容片段（建议前 2000 字）
    返回：
        {
            "title": "AI生成的标题",
            "description": "AI生成的描述",
            "keywords": ["关键词1", "关键词2", ...],
            "visibility": "public",
            "confidence": "high"
        }
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        """AI 分析文档并推荐元数据"""
        serializer = AIClassifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        title = data.get('title', '')
        content = data.get('content', '')

        # 调用公共 AI 分析函数
        ai_result = _ai_classify(title, content)

        if ai_result is None:
            return Response(
                {'detail': 'AI 服务暂时不可用，请手动填写信息', 'code': ErrorCode.AI_UNAVAILABLE.value},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'title': ai_result['title'],
            'description': ai_result['description'],
            'keywords': ai_result['keywords'],
            'visibility': ai_result['visibility'],
            'confidence': ai_result['confidence'],
        })

