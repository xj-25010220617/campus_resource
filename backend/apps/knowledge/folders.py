"""部门资料目录的领域辅助函数。

目录与文档归属使用同一套权限边界：学院级、部门级、校级目录互不串用，
部门管理员只能维护本部门目录。系统目录在首次访问时按固定模板初始化，
让 AI 分类结果有稳定的落点。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from django.db.models import Q

from .models import KnowledgeFolder

# 系统自动分类目录模板：(key, 名称, 子目录)
DEFAULT_FOLDER_TREE: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ('teaching', '教学资料', (
        ('teaching-outline', '课程大纲'),
        ('teaching-lab', '实验指导书'),
        ('teaching-courseware', '课件与习题'),
        ('teaching-exam', '试题库'),
    )),
    ('research', '科研与项目', (
        ('research-project', '项目申报'),
        ('research-result', '成果材料'),
    )),
    ('admin', '行政制度', (
        ('admin-meeting', '会议纪要'),
        ('admin-rules', '规章制度'),
    )),
)

# AI 关键词 → 系统目录。顺序即优先级，先命中先落目录。
KEYWORD_FOLDER_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('会议', '纪要', '例会', '座谈', '议程'), 'admin-meeting'),
    (('制度', '规章', '办法', '规定', '条例', '细则', '通知'), 'admin-rules'),
    (('实验', '实训', '指导书', '操作手册'), 'teaching-lab'),
    (('试题', '试卷', '题库', '答案', '考试', '测验'), 'teaching-exam'),
    (('大纲', '教学计划', '课程标准', '培养方案'), 'teaching-outline'),
    (('课件', '讲义', '习题', '教案', 'ppt', '演示'), 'teaching-courseware'),
    (('科研', '课题', '项目', '申报', '立项', '基金'), 'research-project'),
    (('成果', '论文', '专利', '获奖', '结题'), 'research-result'),
    (('教学', '课程', '课堂', '教材'), 'teaching-courseware'),
)

DEFAULT_MAX_DEPTH = 5


@dataclass(frozen=True)
class FolderScope:
    """当前用户的目录归属范围。"""

    college_id: Optional[int]
    department_id: Optional[int]
    role: str

    @property
    def can_manage(self) -> bool:
        return self.role in ('super_admin', 'college_admin', 'admin', 'dept_admin')

    @property
    def label(self) -> str:
        if self.department_id:
            return 'department'
        if self.college_id:
            return 'college'
        return 'school'

    def q(self) -> Q:
        """仅命中本范围的目录（不含跨级目录）。"""
        if self.department_id:
            return Q(department_id=self.department_id)
        if self.college_id:
            return Q(college_id=self.college_id, department__isnull=True)
        return Q(college__isnull=True, department__isnull=True)

    def values(self) -> dict:
        return {'college_id': self.college_id, 'department_id': self.department_id}


def resolve_folder_scope(user) -> FolderScope:
    """按角色解析目录范围，与文档列表的权限口径保持一致。"""
    role = getattr(user, 'role', 'user')
    if role == 'super_admin':
        return FolderScope(None, None, role)
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


def ensure_default_folders(scope: FolderScope, user=None) -> None:
    """初始化当前范围的系统分类目录（幂等）。"""
    if not scope.can_manage:
        return
    for sort_index, (root_key, root_name, children) in enumerate(DEFAULT_FOLDER_TREE, start=1):
        root, _ = KnowledgeFolder.objects.get_or_create(
            system_key=root_key,
            parent=None,
            **scope.values(),
            defaults={
                'name': root_name,
                'is_system': True,
                'sort_order': sort_index,
                'created_by': user if getattr(user, 'is_authenticated', False) else None,
            },
        )
        for child_index, (child_key, child_name) in enumerate(children, start=1):
            KnowledgeFolder.objects.get_or_create(
                system_key=child_key,
                parent=root,
                **scope.values(),
                defaults={
                    'name': child_name,
                    'is_system': True,
                    'sort_order': sort_index * 100 + child_index,
                    'created_by': user if getattr(user, 'is_authenticated', False) else None,
                },
            )


def find_accessible_folder(folder_id, scope: FolderScope) -> Optional[KnowledgeFolder]:
    """按范围取目录；None / 非法 ID 返回 None。"""
    try:
        folder_id = int(folder_id)
    except (TypeError, ValueError):
        return None
    if folder_id <= 0:
        return None
    return KnowledgeFolder.objects.filter(scope.q(), id=folder_id).first()


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


def suggest_system_folder(
    title: str, keywords: Iterable[str], scope: FolderScope,
) -> Optional[KnowledgeFolder]:
    """根据标题与 AI 关键词推荐系统分类目录。

    仅返回已经初始化在当前范围内的系统目录；无命中时返回 None（保持待分类）。
    """
    haystack = f'{title or ""} {" ".join(k or "" for k in keywords or [])}'.lower()
    if not haystack.strip():
        return None
    matched_key = ''
    for tokens, key in KEYWORD_FOLDER_RULES:
        if any(token.lower() in haystack for token in tokens):
            matched_key = key
            break
    if not matched_key:
        return None
    return KnowledgeFolder.objects.filter(
        scope.q(), system_key=matched_key, is_system=True,
    ).first()


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
