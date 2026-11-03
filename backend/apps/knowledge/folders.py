"""部门资料目录的领域辅助函数。

目录与文档归属使用同一套权限边界：学院级、部门级、校级目录互不串用，
部门管理员只能维护本部门目录。所有目录均由部门用户自行创建，不再初始化
系统默认目录。超级管理员默认可跨部门查看，也可收窄到指定部门。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.db.models import Q

from .models import KnowledgeFolder

DEFAULT_MAX_DEPTH = 5


@dataclass(frozen=True)
class FolderScope:
    """当前用户的目录归属范围。"""

    college_id: Optional[int]
    department_id: Optional[int]
    role: str
    all_departments: bool = False
    # 我的专属空间：目录仅属于该用户本人（owner_id 非空即个人空间）
    owner_id: Optional[int] = None

    @property
    def personal(self) -> bool:
        return self.owner_id is not None

    @property
    def can_manage(self) -> bool:
        return self.role in ('super_admin', 'college_admin', 'admin', 'dept_admin')

    @property
    def label(self) -> str:
        if self.personal:
            return 'personal'
        if self.all_departments:
            return 'school'
        if self.department_id:
            return 'department'
        if self.college_id:
            return 'college'
        return 'school'

    def q(self) -> Q:
        """仅命中本范围的目录（不含跨级目录）。

        个人目录只属于 owner 本人，任何非个人范围都不得命中。
        """
        if self.personal:
            return Q(owner_id=self.owner_id)
        if self.all_departments:
            return Q(owner__isnull=True)
        if self.department_id:
            return Q(department_id=self.department_id, owner__isnull=True)
        if self.college_id:
            return Q(college_id=self.college_id, department__isnull=True, owner__isnull=True)
        return Q(college__isnull=True, department__isnull=True, owner__isnull=True)

    def doc_q(self) -> Q:
        """超级管理员收窄到指定部门/学院时，文档计数也只统计该范围。"""
        if self.personal:
            return Q(uploader_id=self.owner_id, visibility='private')
        if self.all_departments:
            # 专属空间文件只计入个人空间，不混入校级总览的待分类统计
            return ~Q(visibility='private', uploader__role='super_admin')
        if self.role == 'super_admin':
            if self.department_id:
                return Q(department_id=self.department_id)
            if self.college_id:
                return Q(college_id=self.college_id)
        return Q()

    def values(self) -> dict:
        return {
            'college_id': self.college_id,
            'department_id': self.department_id,
            'owner_id': self.owner_id,
        }


def resolve_folder_scope(
    user, department_id=None, college_id=None, personal=False,
) -> FolderScope:
    """按角色解析目录范围，与文档列表的权限口径保持一致。

    仅超级管理员接受显式 ``department_id`` / ``college_id`` / ``personal``：
    传入部门或学院时收窄到对应范围；``personal`` 为真时使用本人的专属空间
    目录；都不传时保持全校范围。其他角色忽略这些参数，继续使用自身归属。
    """
    role = getattr(user, 'role', 'user')
    if personal in (True, '1', 'true', 'True', 'yes'):
        personal = True
    else:
        personal = False
    if role == 'super_admin':
        if personal:
            return FolderScope(
                None, None, role, all_departments=False, owner_id=user.id,
            )
        if department_id not in (None, '', '0', 0):
            try:
                scoped_id = int(department_id)
            except (TypeError, ValueError) as exc:
                raise ValueError('部门 ID 不合法') from exc
            return FolderScope(None, scoped_id, role, all_departments=False)
        if college_id not in (None, '', '0', 0):
            try:
                scoped_college_id = int(college_id)
            except (TypeError, ValueError) as exc:
                raise ValueError('学院 ID 不合法') from exc
            return FolderScope(scoped_college_id, None, role, all_departments=False)
        return FolderScope(None, None, role, all_departments=True)
    if role in ('college_admin', 'admin'):
        return FolderScope(getattr(user, 'college_id', None), None, role)
    if role == 'dept_admin':
        return FolderScope(getattr(user, 'college_id', None), getattr(user, 'department_id', None), role)
    # reviewer / user：有部门归属时只看本部门目录，否则只看学院目录；均不可维护
    department_id = getattr(user, 'department_id', None)
    college_id = getattr(user, 'college_id', None)
    if department_id:
        return FolderScope(college_id, department_id, role)
    return FolderScope(college_id, None, role)


def folder_path_map(folders: Iterable[KnowledgeFolder]) -> dict[int, str]:
    """一次性计算目录完整路径，避免列表序列化产生 N+1 查询。"""
    by_id = {folder.id: folder for folder in folders}
    paths: dict[int, str] = {}

    def build(folder: KnowledgeFolder, seen: frozenset[int] = frozenset()) -> str:
        if folder.id in paths:
            return paths[folder.id]
        if not folder.parent_id or folder.parent_id not in by_id or folder.parent_id in seen:
            path = folder.name
        else:
            path = f'{build(by_id[folder.parent_id], seen | {folder.id})} / {folder.name}'
        paths[folder.id] = path
        return path

    for item in by_id.values():
        build(item)
    return paths


def folder_subtree_ids(folder_id) -> list[int]:
    """返回目录自身及全部后代目录 ID；上级目录筛选时一并包含子目录文件。"""
    try:
        root_id = int(folder_id)
    except (TypeError, ValueError):
        return []
    children: dict[Optional[int], list[int]] = {}
    for pk, parent_id in KnowledgeFolder.objects.values_list('id', 'parent_id'):
        children.setdefault(parent_id, []).append(pk)
    result = [root_id]
    seen = {root_id}
    frontier = [root_id]
    while frontier:
        current = frontier.pop()
        for child_id in children.get(current, []):
            if child_id in seen:
                continue
            seen.add(child_id)
            result.append(child_id)
            frontier.append(child_id)
    return result


def rollup_folder_counts(
    folders: Iterable[KnowledgeFolder],
    direct_counts: dict[int, int],
) -> dict[int, int]:
    """把每个目录的文件数累加到全部上级目录，使上级目录包含子目录文件。"""
    parent_map = {folder.id: folder.parent_id for folder in folders}
    totals: dict[int, int] = {}
    for folder_id, count in direct_counts.items():
        current: Optional[int] = folder_id
        seen: set[int] = set()
        while current is not None and current not in seen:
            seen.add(current)
            totals[current] = totals.get(current, 0) + count
            current = parent_map.get(current)
    return totals


def folder_depth(folder: KnowledgeFolder) -> int:
    """目录层级，根目录为 1。"""
    depth = 1
    parent = folder.parent
    seen: set[int] = {folder.id}
    while parent is not None and parent.id not in seen:
        depth += 1
        seen.add(parent.id)
        parent = parent.parent
    return depth


def find_accessible_folder(folder_id, scope: FolderScope) -> Optional[KnowledgeFolder]:
    """按范围取目录；None / 非法 ID 返回 None。"""
    try:
        folder_id = int(folder_id)
    except (TypeError, ValueError):
        return None
    if folder_id <= 0:
        return None
    return KnowledgeFolder.objects.filter(scope.q(), is_system=False, id=folder_id).first()


def validate_folder_parent(parent_id, scope: FolderScope) -> Optional[KnowledgeFolder]:
    """校验新建目录的上级目录。"""
    if parent_id in (None, '', 0, '0'):
        return None
    parent = find_accessible_folder(parent_id, scope)
    if parent is None:
        raise ValueError('上级目录不存在或无权访问')
    if folder_depth(parent) >= DEFAULT_MAX_DEPTH:
        raise ValueError(f'目录最多支持 {DEFAULT_MAX_DEPTH} 级')
    return parent


def sibling_name_exists(
    name: str, parent: Optional[KnowledgeFolder], scope: FolderScope,
    exclude_id: Optional[int] = None,
) -> bool:
    qs = KnowledgeFolder.objects.filter(scope.q(), parent=parent, name=name)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def folder_tree_payload(folders: list[KnowledgeFolder]) -> list[dict]:
    """把目录列表整理为前端树，便于一次响应完成渲染。"""
    by_id = {folder.id: folder for folder in folders}
    children: dict[Optional[int], list[KnowledgeFolder]] = {}
    for folder in folders:
        parent_id = folder.parent_id if folder.parent_id in by_id else None
        children.setdefault(parent_id, []).append(folder)

    def serialize(folder: KnowledgeFolder) -> dict:
        child_items = [
            serialize(child) for child in children.get(folder.id, [])
        ]
        return {
            'id': folder.id,
            'name': folder.name,
            'parent_id': folder.parent_id if folder.parent_id in by_id else None,
            'is_system': folder.is_system,
            'system_key': folder.system_key,
            'sort_order': folder.sort_order,
            'children': child_items,
        }

    return [serialize(item) for item in children.get(None, [])]
