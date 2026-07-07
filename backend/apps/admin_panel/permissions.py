"""
Admin Panel - 权限工具与范围过滤

- IsAnyAdmin: 任何一种管理员（超管/学院管理员/部门管理员/兼容 admin）
- scope_users: 用户表范围过滤（不同角色看不同范围的用户）
- scope_resources: 资源表范围过滤（互斥归属）
- scope_upload_logs: 上传日志范围过滤
- assert_can_manage_user / assert_can_manage_resource: 对象级权限断言
"""
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied


ADMIN_ROLES = {'super_admin', 'college_admin', 'dept_admin', 'admin'}


class IsAnyAdmin(permissions.BasePermission):
    """任何一种管理员都可访问"""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ADMIN_ROLES)


def scope_users(qs, u):
    """用户表范围过滤：不同角色看到不同范围的用户"""
    if u.role == 'super_admin':
        return qs
    if u.role in ('admin', 'college_admin'):
        # 学院管理员：只看本学院的 role=user（学生）
        return qs.filter(college_id=u.college_id, role='user')
    if u.role == 'dept_admin':
        # 部门管理员：只看本部门的所有 user（员工）
        return qs.filter(department_id=u.department_id)
    return qs.none()


def scope_resources(qs, u):
    """资源表范围过滤（互斥归属）：
    - 学院资源归属 college，department 为空
    - 部门资源归属 department，college 为空

    注意：本函数只做"管理范围"过滤，不过滤 status。
    前台浏览请使用 scope_resources_for_browse。
    """
    if u.role == 'super_admin':
        return qs
    if u.role in ('admin', 'college_admin'):
        return qs.filter(college_id=u.college_id, department_id__isnull=True)
    if u.role == 'dept_admin':
        return qs.filter(department_id=u.department_id, college_id__isnull=True)
    return qs.none()


def scope_resources_for_browse(qs, u):
    """资源浏览范围过滤（用户前台视角）：

    - super_admin：全校已发布资源
    - admin / college_admin：本学院已发布资源（department IS NULL）
    - dept_admin：本部门已发布资源（college IS NULL）
    - user：什么都看不到，只能通过 /api/chat/ask/ AI 检索

    与 scope_resources 的区别：本函数强制 status='published'（浏览只看已发布），
    且 user 直接返回 qs.none()（不允许直接浏览资源列表）。
    """
    if u.role == 'super_admin':
        return qs.filter(status='published')
    if u.role in ('admin', 'college_admin'):
        return qs.filter(
            status='published',
            college_id=u.college_id,
            department_id__isnull=True,
        )
    if u.role == 'dept_admin':
        return qs.filter(
            status='published',
            department_id=u.department_id,
            college_id__isnull=True,
        )
    return qs.none()


def scope_upload_logs(qs, u):
    """上传日志范围过滤（与 scope_resources 同语义）"""
    if u.role == 'super_admin':
        return qs
    if u.role in ('admin', 'college_admin'):
        return qs.filter(college_id=u.college_id, department_id__isnull=True)
    if u.role == 'dept_admin':
        return qs.filter(department_id=u.department_id, college_id__isnull=True)
    return qs.none()


def assert_can_manage_user(target, u):
    """断言：管理员 u 有权管理 target 用户。

    规则：
    - super_admin: 可管理所有人（含同级，但 target 不能是自己）
    - admin / college_admin: 仅能管理本学院 role=user 的学生
    - dept_admin: 仅能管理本部门 role=user 的员工
    """
    if u.role == 'super_admin':
        if target.id == u.id:
            raise PermissionDenied('不能对自己操作')
        return
    if u.role in ('admin', 'college_admin'):
        if target.role != 'user' or target.college_id != u.college_id:
            raise PermissionDenied('无权管理该用户')
        return
    if u.role == 'dept_admin':
        if target.role != 'user' or target.department_id != u.department_id:
            raise PermissionDenied('无权管理该用户')
        return
    raise PermissionDenied('非管理员，无权操作')


def assert_can_manage_resource(target, u):
    """断言：管理员 u 有权管理 target 资源"""
    if u.role == 'super_admin':
        return
    if u.role in ('admin', 'college_admin'):
        if target.college_id != u.college_id or target.department_id:
            raise PermissionDenied('无权管理该资源')
        return
    if u.role == 'dept_admin':
        if target.department_id != u.department_id or target.college_id:
            raise PermissionDenied('无权管理该资源')
        return
    raise PermissionDenied('非管理员，无权操作')


def manage_scope_label(u):
    """当前用户的管理范围标签"""
    if u.role == 'super_admin':
        return 'all'
    if u.role in ('admin', 'college_admin'):
        return 'college'
    if u.role == 'dept_admin':
        return 'department'
    return 'self'