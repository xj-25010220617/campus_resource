"""部门资料目录接口：目录树维护与文档归档。"""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema

from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsAdminOrSuperAdmin

from apps.accounts.models import College
from apps.classify.models import Department

from .folders import (
    find_accessible_folder,
    folder_path_map,
    resolve_folder_scope,
    rollup_folder_counts,
    sibling_name_exists,
    validate_folder_parent,
)
from .models import KnowledgeDoc, KnowledgeFolder
from .views import KnowledgeDocListView


def _folder_payload(folder: KnowledgeFolder, paths: dict, counts: dict) -> dict:
    return {
        'id': folder.id,
        'name': folder.name,
        'parent_id': folder.parent_id,
        'path': paths.get(folder.id, folder.name),
        'is_system': folder.is_system,
        'system_key': folder.system_key,
        'sort_order': folder.sort_order,
        'doc_count': counts.get(folder.id, 0),
        'created_by_name': (
            folder.created_by.username if folder.created_by_id and folder.created_by else ''
        ),
        'created_at': folder.created_at.isoformat() if folder.created_at else '',
    }


def _collect_subtree_ids(folder: KnowledgeFolder, folders: list[KnowledgeFolder]) -> list[int]:
    ids = [folder.id]
    frontier = [folder.id]
    while frontier:
        parent_id = frontier.pop()
        for item in folders:
            if item.parent_id == parent_id:
                ids.append(item.id)
                frontier.append(item.id)
    return ids


class FolderListCreateView(APIView):
    """GET /api/knowledge/folders/ 目录列表；POST 新建目录。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            scope = resolve_folder_scope(
                request.user,
                department_id=request.query_params.get('department_id'),
                college_id=request.query_params.get('college_id'),
                personal=request.query_params.get('personal'),
            )
        except ValueError as exc:
            raise ValidationError({'scope': str(exc)}) from exc

        # 旧版 AI/系统目录不再展示；若尚未执行清理迁移，其下文件按待分类统计
        folders = list(
            KnowledgeFolder.objects.filter(scope.q(), is_system=False)
            .select_related('created_by')
            .order_by('sort_order', 'id')
        )
        paths = folder_path_map(folders)

        scoped_docs = KnowledgeDocListView._admin_scope_filter(
            KnowledgeDoc.objects.all(), request.user,
        ).filter(scope.doc_q())
        # 子目录中的文件同时计入上级目录（教学资料包含课程大纲下的文件）
        counts = rollup_folder_counts(
            folders,
            {
                row['folder_id']: row['total']
                for row in scoped_docs.filter(folder__is_system=False)
                .exclude(folder__isnull=True)
                .values('folder_id')
                .annotate(total=Count('id'))
            },
        )
        unfiled_count = scoped_docs.filter(
            Q(folder__isnull=True) | Q(folder__is_system=True),
        ).count()

        return Response({
            'folders': [_folder_payload(item, paths, counts) for item in folders],
            'unfiled_count': unfiled_count,
            'total_docs': scoped_docs.count(),
            'scope': {
                'kind': scope.label,
                'college_id': scope.college_id,
                'department_id': scope.department_id,
                'all_departments': scope.all_departments,
                'personal': scope.personal,
                'can_manage': scope.can_manage,
            },
        })

    def post(self, request):
        try:
            scope = resolve_folder_scope(
                request.user,
                department_id=request.data.get('department_id'),
                college_id=request.data.get('college_id'),
                personal=request.data.get('personal'),
            )
        except ValueError as exc:
            raise ValidationError({'scope': str(exc)}) from exc
        if not scope.can_manage:
            raise PermissionDenied('当前账号无权维护资料目录')
        if scope.all_departments:
            raise ValidationError({'department_id': '请先选择具体学院或部门，再创建目录'})
        if scope.role == 'super_admin':
            if scope.department_id and not Department.objects.filter(
                id=scope.department_id,
            ).exists():
                raise ValidationError({'department_id': '部门不存在'})
            if scope.college_id and not College.objects.filter(
                id=scope.college_id,
            ).exists():
                raise ValidationError({'college_id': '学院不存在'})

        name = str(request.data.get('name', '')).strip()
        if not name:
            raise ValidationError({'name': '目录名称不能为空'})
        if len(name) > 64:
            raise ValidationError({'name': '目录名称不能超过 64 个字符'})

        try:
            parent = validate_folder_parent(request.data.get('parent_id'), scope)
        except ValueError as exc:
            raise ValidationError({'parent_id': str(exc)}) from exc

        if sibling_name_exists(name, parent, scope):
            raise ValidationError({'name': '同级目录下已存在同名文件夹'})

        folder = KnowledgeFolder.objects.create(
            name=name,
            parent=parent,
            is_system=False,
            sort_order=parent.sort_order + 1 if parent else 9999,
            created_by=request.user,
            **scope.values(),
        )
        paths = folder_path_map(list(KnowledgeFolder.objects.filter(scope.q())))
        return Response(_folder_payload(folder, paths, {}), status=status.HTTP_201_CREATED)


class FolderDetailView(APIView):
    """PATCH /api/knowledge/folders/{id}/ 重命名；DELETE 删除目录。"""

    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_object(self, request, pk):
        scope = resolve_folder_scope(
            request.user,
            personal=request.query_params.get('personal'),
        )
        folder = find_accessible_folder(pk, scope)
        if folder is None:
            raise ValidationError({'folder_id': '资料目录不存在或无权访问'})
        return folder, scope

    def patch(self, request, pk):
        folder, scope = self.get_object(request, pk)
        name = str(request.data.get('name', '')).strip()
        if not name:
            raise ValidationError({'name': '目录名称不能为空'})
        if len(name) > 64:
            raise ValidationError({'name': '目录名称不能超过 64 个字符'})
        if sibling_name_exists(name, folder.parent, scope, exclude_id=folder.id):
            raise ValidationError({'name': '同级目录下已存在同名文件夹'})
        folder.name = name
        folder.save(update_fields=['name', 'updated_at'])
        paths = folder_path_map(list(KnowledgeFolder.objects.filter(scope.q())))
        return Response(_folder_payload(folder, paths, {}))

    def delete(self, request, pk):
        folder, scope = self.get_object(request, pk)

        with transaction.atomic():
            all_folders = list(KnowledgeFolder.objects.filter(scope.q()))
            subtree_ids = _collect_subtree_ids(folder, all_folders)
            moved_count = KnowledgeDoc.objects.filter(folder_id__in=subtree_ids).update(
                folder=folder.parent,
            )
            KnowledgeFolder.objects.filter(id__in=subtree_ids).delete()

        return Response({
            'detail': '目录已删除，其中文件已移至上级目录',
            'deleted_folder_count': len(subtree_ids),
            'moved_doc_count': moved_count,
        })


class DocMoveView(APIView):
    """POST /api/knowledge/docs/{id}/move/ 将单个文档归档到目录。"""

    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request, pk):
        scope = resolve_folder_scope(
            request.user, personal=request.data.get('personal'),
        )
        folder_id = request.data.get('folder_id')
        folder = find_accessible_folder(folder_id, scope) if folder_id not in (None, '', 0, '0') else None
        if folder_id not in (None, '', 0, '0') and folder is None:
            raise ValidationError({'folder_id': '资料目录不存在或无权访问'})

        queryset = KnowledgeDocListView._admin_scope_filter(KnowledgeDoc.objects.all(), request.user)
        if scope.personal:
            # 个人目录只收纳本人的私密文件
            queryset = queryset.filter(scope.doc_q())
        doc = get_object_or_404(queryset, id=pk)
        doc.folder = folder
        doc.save(update_fields=['folder', 'updated_at'])
        return Response({
            'detail': '已移动',
            'doc_id': doc.id,
            'folder_id': folder.id if folder else None,
            'folder_name': folder.name if folder else '',
        })


class DocBatchMoveView(APIView):
    """POST /api/knowledge/docs/batch-move/ 批量归档文档。"""

    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def post(self, request):
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'ids': '请选择要移动的文档'})
        if len(ids) > 100:
            raise ValidationError({'ids': '单次最多移动 100 个文档'})
        try:
            ids = [int(item) for item in ids]
        except (TypeError, ValueError) as exc:
            raise ValidationError({'ids': '文档 ID 不合法'}) from exc

        scope = resolve_folder_scope(
            request.user, personal=request.data.get('personal'),
        )
        raw_folder_id = request.data.get('folder_id')
        folder = (
            find_accessible_folder(raw_folder_id, scope)
            if raw_folder_id not in (None, '', 0, '0') else None
        )
        if raw_folder_id not in (None, '', 0, '0') and folder is None:
            raise ValidationError({'folder_id': '资料目录不存在或无权访问'})

        queryset = KnowledgeDocListView._admin_scope_filter(KnowledgeDoc.objects.all(), request.user)
        if scope.personal:
            # 个人目录只收纳本人的私密文件
            queryset = queryset.filter(scope.doc_q())
        docs = queryset.filter(id__in=ids)
        moved = docs.update(folder=folder)
        return Response({
            'detail': f'已移动 {moved} 个文档',
            'moved_count': moved,
            'folder_id': folder.id if folder else None,
            'folder_name': folder.name if folder else '',
        })
