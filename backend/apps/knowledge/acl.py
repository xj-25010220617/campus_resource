"""知识文档可见范围（ACL）的公共判定。

文档归属曾使用 scope（school/college/department）字段，后改为由
college_id/department_id 自动确定；scope 字段被保留但已废弃。若只按
"college_id 与 department_id 均为空"就判定为校级，会把 scope=college/
department 但归属字段未回填的历史文档当成全校公开，暴露给所有部门管理员。

因此校级文档必须同时满足：无学院/部门归属，且没有残留的旧归属标记。
历史遗留的非校级文档按 fail-closed 处理：仅上传者本人可见。
"""
from django.db.models import Q


def school_level_q():
    """校级文档的查询条件：无学院/部门归属，且没有残留的旧 scope 标记。"""
    return (
        Q(college_id__isnull=True, department_id__isnull=True)
        & (Q(scope__isnull=True) | Q(scope='') | Q(scope='school'))
    )
