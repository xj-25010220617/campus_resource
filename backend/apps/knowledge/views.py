import django_filters
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import transaction
from django.shortcuts import get_object_or_404

from config.permissions import IsAdminOrSuperAdmin, IsLoginUser, IsOwnerOrReadOnly
from utils.oss import oss_client

from .models import Keyword, KnowledgeDoc
from .serializers import (
    KeywordCreateSerializer,
    KeywordSerializer,
    KnowledgeDocListSerializer,
    KnowledgeDocSerializer,
    UploadCallbackSerializer,
    UploadSTSRequestSerializer,
)


class KnowledgeDocFilter(django_filters.FilterSet):
    """知识文档多条件精确过滤器"""
    college_id = django_filters.NumberFilter(field_name='college_id')
    discipline_id = django_filters.NumberFilter(field_name='discipline_id')
    category_id = django_filters.NumberFilter(field_name='category_id')
    file_type = django_filters.CharFilter(field_name='file_type')
    status = django_filters.CharFilter(field_name='status')
    source_type = django_filters.CharFilter(field_name='source_type')

    class Meta:
        model = KnowledgeDoc
        fields = [
            'college_id', 'discipline_id', 'category_id',
            'file_type', 'status', 'source_type',
        ]


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
        - category_id (int, optional): 按资源类型ID精确筛选
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
    permission_classes = [IsLoginUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = KnowledgeDocFilter
    search_fields = ['title', 'description']

    def get_queryset(self):
        user = self.request.user
        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'uploader',
        ).all()

        if user.role == 'super_admin':
            # 超管：可查看平台全部文档
            pass
        elif user.role == 'admin':
            # 学院管理员：仅查看所属学院文档
            if user.college_id:
                queryset = queryset.filter(college_id=user.college_id)
            else:
                return queryset.none()
        else:
            # 普通用户：仅查看本人上传文档
            queryset = queryset.filter(uploader=user)

        return queryset.order_by('-created_at')


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
        - category_id (int, optional): 资源类型 ID
        - freshness_date (str, optional): 内容截止日期（格式 YYYY-MM-DD）
    权限规则：
        - super_admin（超级管理员）: 可查看/编辑平台全部文档
        - admin（学院管理员）: 仅能查看/编辑自己所属学院下的文档
        - 普通登录用户: 仅能查看/编辑本人上传的文档
    返回：
        - GET: 文档完整对象，包含 id / title / description / source_type / source_url /
          oss_key / oss_url / file_name / file_type / file_size / md5_hash / page_count /
          discipline / discipline_name / category / category_name /
          college / college_name / uploader / uploader_name /
          status / chunk_count / is_sensitive / sensitive_checked /
          freshness_date / created_at / updated_at / indexed_at
        - PUT: 更新后的文档完整对象
    """
    serializer_class = KnowledgeDocSerializer
    permission_classes = [IsLoginUser]

    def get_queryset(self):
        user = self.request.user
        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'category', 'uploader',
        ).all()

        if user.role == 'super_admin':
            # 超管：可查看/编辑全部文档
            pass
        elif user.role == 'admin':
            # 学院管理员：仅查看/编辑所属学院文档
            if user.college_id:
                queryset = queryset.filter(college_id=user.college_id)
            else:
                return queryset.none()
        else:
            # 普通用户：仅查看/编辑本人上传文档
            queryset = queryset.filter(uploader=user)

        return queryset


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
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]

    def get_queryset(self):
        return KnowledgeDoc.objects.all()

    def perform_destroy(self, instance):
        # 1. 删除 OSS 文件（含容错，失败不阻塞数据库删除）
        if instance.oss_key:
            deleted = oss_client.delete(instance.oss_key)
            if not deleted:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f'OSS 文件删除失败 oss_key={instance.oss_key}，跳过')

        # 2. 删除 MySQL 记录
        #    CASCADE 自动级联：KnowledgeDoc → Keyword + DocChunk
        #    TODO: 同步删除 Milvus 向量（甲同学完成后集成）
        #    from utils.milvus import milvus_client
        #    milvus_client.delete_by_doc_id(instance.id)
        with transaction.atomic():
            instance.delete()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': '删除成功'})


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
        if user.role == 'super_admin':
            return KnowledgeDoc.objects.all()
        return KnowledgeDoc.objects.filter(uploader=user)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        download_url = oss_client.get_download_url(instance.oss_key)
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
    功能：返回指定文档的所有关键词，按权重（weight）降序排列
    权限规则：
        - super_admin（超级管理员）: 可查看任意文档的关键词
        - admin（学院管理员）: 仅能查看自己上传的文档的关键词
    返回：关键词数组，每条包含 id / phrase / match_type / weight / created_by_name / created_at
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request, doc_id):
        """返回文档的关键词列表"""
        doc = get_object_or_404(KnowledgeDoc, id=doc_id)

        # 权限校验：超管全放行，其余仅允许查看自己上传的文档
        if request.user.role != 'super_admin' and doc.uploader_id != request.user.id:
            raise PermissionDenied('只能查看自己上传的文档的关键词')

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
                {'detail': f'关键词 "{data["phrase"]}" 已存在于此文档中', 'code': 'CONFLICT'},
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

        # 获取 STS 临时凭证
        credentials = oss_client.get_sts_credentials(file_name, college_id)
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
        - title (str, required): 文档标题
        - description (str, required): 文档描述
        - file_name (str, required): 原始文件名
        - file_size (int, required): 文件大小（字节）
        - file_type (str, required): 文件类型
        - md5 (str, required): 文件 MD5 哈希值
        - discipline_id (int, optional): 学科方向 ID
        - category_id (int, optional): 资源类型 ID
        - college_id (int, optional): 所属学院 ID（默认取上传者所在学院）
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

        # 再次 MD5 去重（防止并发上传导致重复）
        duplicate_doc = oss_client.check_duplicate(data['md5'])
        if duplicate_doc:
            return Response({
                'id': duplicate_doc.id,
                'status': duplicate_doc.status,
                'duplicate': True,
            })

        # 创建文档记录
        new_doc = KnowledgeDoc.objects.create(
            oss_key=data['oss_key'],
            title=data['title'],
            description=data.get('description', ''),
            file_name=data['file_name'],
            file_size=data['file_size'],
            file_type=data['file_type'],
            md5_hash=data['md5'],
            source_type='upload',
            uploader=request.user,
            status='uploaded',
            college_id=(
                data['college_id']
                if data.get('college_id') is not None
                else request.user.college_id
            ),
            discipline_id=data.get('discipline_id'),
            category_id=data.get('category_id'),
        )

        # TODO: 调用 Celery 异步索引任务（甲同学完成后集成）
        # index_document.delay(doc_id=new_doc.id)

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
        })

    @staticmethod
    def _validate_oss_key(oss_key: str) -> None:
        """校验 oss_key 格式合法性，防止路径穿越攻击"""
        if '..' in oss_key:
            raise ValidationError({'oss_key': '包含非法路径字符 ".."'})
        if oss_key.startswith('/'):
            raise ValidationError({'oss_key': '不能以 "/" 开头'})
        if not oss_key.startswith('knowledge/'):
            raise ValidationError({'oss_key': 'OSS Key 格式不合法，必须以 "knowledge/" 开头'})
