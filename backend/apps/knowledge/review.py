"""文档审核公共逻辑。

把三件在 4 个上传入口都会用到的事情集中在这里，避免重复实现：

1. 上传时的初始审核状态：super_admin 上传免审，其余管理员一律 pending
2. 非超管上传的归属校验：既没有学院也没有部门的账号不允许上传，
   否则文档没有任何审核人能接（旧 admin 兼容角色可能出现这种组合）
3. 审核通过后才派发解析 / 向量化 / 自动关联任务（"先审后索引"的落点）
"""
from django.db.models import Q

# 审核状态常量（与 KnowledgeDoc.REVIEW_STATUS_CHOICES 对应）
PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'

REVIEWER_ROLE = 'reviewer'


def initial_review_status(user) -> str:
    """上传时的初始审核状态：超管上传直接通过，其余管理员待审核。"""
    return APPROVED if user.role == 'super_admin' else PENDING


def ensure_reviewable_scope(user, college_id, department_id) -> None:
    """非超管上传必须能落到「部门」或「学院」之一。

    两者都为空时，文档既不属于任何部门也不属于任何学院，
    没有任何审核员能接单，因此直接拒绝上传。
    """
    from rest_framework.exceptions import ValidationError

    if user.role == 'super_admin':
        return
    if not college_id and not department_id:
        raise ValidationError({
            'college_id': '当前账号未归属任何学院或部门，无法上传（无对应审核人）',
        })


def reviewer_scope_q(user) -> Q:
    """审核员的审核范围：

    - 绑部门：本部门上传的文档
    - 绑学院：本学院「学院级」文档（department_id 为空），不抢部门审核员的活
    - 其他：无范围（拒绝一切）
    """
    if user.department_id:
        return Q(department_id=user.department_id)
    if user.college_id:
        return Q(college_id=user.college_id, department_id__isnull=True)
    return Q(pk__in=[])


def reviewer_can_review(user, doc) -> bool:
    """单个文档是否落在审核员范围内（不查库，用内存判断）。"""
    if user.department_id:
        return doc.department_id == user.department_id
    if user.college_id:
        return doc.college_id == user.college_id and doc.department_id is None
    return False


def dispatch_index_tasks(doc_id: int) -> None:
    """派发上传后的三个异步任务：发布日期提取、索引（切片+向量化）、AI 自动关联。

    仅在文档审核通过（或超管上传免审）时调用：审核未通过的文档不消耗算力。
    """
    from utils.celery_dispatcher import safe_delay

    from .tasks import auto_link_relations_task, extract_freshness_task, index_document_task

    safe_delay(extract_freshness_task, doc_id)
    safe_delay(index_document_task, doc_id)
    safe_delay(auto_link_relations_task, doc_id)
