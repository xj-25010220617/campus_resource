import django_filters
import logging
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView


from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.accounts.models import College
from apps.classify.models import Category, Discipline
from config.permissions import IsAdminOrSuperAdmin, IsLoginUser, IsOwnerOrReadOnly
from utils.oss import oss_client

from .extractor import extract_freshness_for_doc
from .models import Keyword, KnowledgeDoc
from .serializers import (
    KeywordCreateSerializer,
    KeywordSerializer,
    KnowledgeDocListSerializer,
    KnowledgeDocSerializer,
    TextUploadSerializer,
    UploadCallbackSerializer,
    UploadSTSRequestSerializer,
)

logger = logging.getLogger(__name__)


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
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = KnowledgeDocFilter
    search_fields = ['title', 'description']

    def get_queryset(self):
        user = self.request.user
        latest_only = self.request.query_params.get('latest_only', 'true').lower() == 'true'

        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'uploader',
        ).all()

        # 可见性权限过滤
        queryset = self._apply_visibility_filter(queryset, user)

        if latest_only:
            # 按学院+学科+分类分组，取每组最新一条
            grouped = (
                queryset
                .values('college_id', 'discipline_id', 'category_id')
                .annotate(max_created=Max('created_at'))
            )
            latest_ids = []
            for group in grouped:
                latest_id = (
                    queryset
                    .filter(
                        college_id=group['college_id'],
                        discipline_id=group['discipline_id'],
                        category_id=group['category_id'],
                        created_at=group['max_created'],
                    )
                    .values_list('id', flat=True)
                    .first()
                )
                if latest_id:
                    latest_ids.append(latest_id)
            queryset = queryset.filter(id__in=latest_ids)

        return queryset.order_by('-created_at')

    @staticmethod
    def _apply_visibility_filter(queryset, user):
        """按用户角色过滤可见性：超管全量，管理员本院+自己，普通用户公开+自己"""
        if user.role == 'super_admin':
            return queryset
        if user.role == 'admin':
            return queryset.filter(
                Q(visibility='public')
                | Q(visibility='private', college_id=user.college_id)
                | Q(visibility='private', uploader_id=user.id),
            )
        return queryset.filter(
            Q(visibility='public')
            | Q(visibility='private', uploader_id=user.id),
        )


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
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = KnowledgeDoc.objects.select_related(
            'college', 'discipline', 'category', 'uploader',
        ).all()
        return KnowledgeDocListView._apply_visibility_filter(queryset, user)

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
        queryset = KnowledgeDoc.objects.all()
        return KnowledgeDocListView._apply_visibility_filter(queryset, user)

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
    功能：返回指定文档的所有关键词，按权重（weight）降序排列，所有登录用户均可查看
    返回：关键词数组，每条包含 id / phrase / match_type / weight / created_by_name / created_at
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, doc_id):
        """返回文档的关键词列表"""
        doc = get_object_or_404(KnowledgeDoc, id=doc_id)
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

        # 校验分类关联数据合法性
        self._validate_classification(data)

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
            college_id=data['college_id'],
            discipline_id=data['discipline_id'],
            category_id=data['category_id'],
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
        from .tasks import extract_freshness_task
        safe_delay(extract_freshness_task, new_doc.id)

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
        })

    @staticmethod
    def _validate_classification(data: dict) -> None:
        """校验分类关联数据——college_id / discipline_id 存在，category_id 必须是二级分类"""
        # 校验 college_id 存在
        if not College.objects.filter(id=data['college_id']).exists():
            raise ValidationError({'college_id': '学院不存在'})

        # 校验 discipline_id 存在
        if not Discipline.objects.filter(id=data['discipline_id']).exists():
            raise ValidationError({'discipline_id': '学科方向不存在'})

        # 校验 category_id 存在且必须是二级分类（有 parent）
        try:
            category = Category.objects.get(id=data['category_id'])
        except Category.DoesNotExist:
            raise ValidationError({'category_id': '资源类型不存在'})
        if category.parent is None:
            raise ValidationError({'category_id': '必须是二级分类，不能选择一级分类'})

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
    管理员手动录入文本

    POST /api/knowledge/upload/text/
    功能：管理员手动输入文本内容，后端直传 OSS 并创建知识文档记录
    请求参数：
        - title (str, required): 文档标题
        - content (str, required): 文本内容
        - description (str, optional): 文档描述
        - college_id (int, required): 学院 ID
        - discipline_id (int, required): 学科方向 ID
        - category_id (int, required): 资源类型 ID（必须是二级分类）
        - keywords (list[str], optional): 关键词列表
    返回：{"id": <doc.id>, "status": "uploaded", "file_name": "<title>.txt"}
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        """处理文本录入请求"""
        import hashlib
        import uuid
        from datetime import datetime

        serializer = TextUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # 校验分类关联数据合法性（复用 UploadCallbackView 的校验逻辑）
        UploadCallbackView._validate_classification(data)

        # 生成 OSS 对象 Key
        now = datetime.now()
        oss_key = f'knowledge/{data["college_id"]}/{now:%Y-%m}/{uuid.uuid4().hex[:12]}.txt'

        # 上传文本到 OSS
        content = data['content']
        success = oss_client.upload_text(content, oss_key)
        if not success:
            return Response(
                {'detail': '文本上传到 OSS 失败，请稍后重试', 'code': 'UPLOAD_FAILED'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 计算 MD5
        md5_hash = hashlib.md5(content.encode('utf-8')).hexdigest()

        # 生成 OSS 访问 URL
        oss_url = f'https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}'

        # 创建 KnowledgeDoc 记录
        new_doc = KnowledgeDoc.objects.create(
            title=data['title'],
            description=data.get('description', ''),
            source_type='upload',
            oss_key=oss_key,
            oss_url=oss_url,
            file_name=f'{data["title"]}.txt',
            file_type='txt',
            file_size=len(content.encode('utf-8')),
            md5_hash=md5_hash,
            college_id=data['college_id'],
            discipline_id=data['discipline_id'],
            category_id=data['category_id'],
            uploader=request.user,
            status='uploaded',
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

        return Response({
            'id': new_doc.id,
            'status': new_doc.status,
            'file_name': new_doc.file_name,
        })


class KnowledgeDocPreviewView(APIView):
    """
    文档内容预览接口

    GET /api/knowledge/docs/{id}/preview/
    功能：根据文件类型返回预览内容——文本类返回原文，音视频/图片/文档返回 OSS 签名预览 URL
    权限规则：所有登录用户均可预览
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
        doc = get_object_or_404(
            KnowledgeDoc.objects.select_related('uploader'), id=pk,
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
        import requests
        download_url = oss_client.get_download_url(doc.oss_key, expires=60)
        try:
            resp = requests.get(download_url, timeout=10)
            resp.raise_for_status()
            text_content = resp.text
        except Exception:
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
        - 200 OK: 单个对象 {freshness_date, confidence, source_excerpt, raw_response}
    错误码：
        - 401 UNAUTHORIZED: 未登录
        - 403 PERMISSION_DENIED: 越权触发
        - 404 NOT_FOUND: 文档不存在
    注意：
        - 图片/视频/音频类型无法抽取文本，confidence='none'
        - 失败时 freshness_date 留空，前端可再次手动触发
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
                {'detail': '文档不存在', 'code': 'NOT_FOUND'},
                status=status.HTTP_404_NOT_FOUND,
            )
        # 200 返回单个对象（按 CONVENTIONS.md 第 8 节："返回单个对象"）
        return Response({
            'freshness_date': result.freshness_date,
            'confidence': result.confidence,
            'source_excerpt': result.source_excerpt,
            'raw_response': result.raw_response,
        })

    @staticmethod
    def _raise_permission_error(message: str):
        raise PermissionDenied({'detail': message, 'code': 'PERMISSION_DENIED'})

