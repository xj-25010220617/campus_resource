"""索引前置安全校验（集中式唯一入口）

为什么必须放在任务体内
----------------------
`index_document_task` 的执行路径不止"视图 → Celery"一条：

    view        → safe_delay → (Celery 异步)   → task
    view        → safe_delay → (Redis 不可用)   → task 同步执行
    utils/crawler.py            → safe_delay    → task
    scripts/index_all_docs.py   → task.run()    → task
    scripts/index_quick.py      → task.run()    → task
    broker 中残留的消息                          → task

因此任何安全门只要写在视图层就必然存在绕过路径。本模块提供唯一入口，
由 [`apps.knowledge.tasks.index_document_task`](tasks.py) 在**任务体开头**调用，
所有上述路径自动收敛到同一条校验上。

P1-1 阶段边界
-------------
当前项目**尚无** ContentModeration / moderation_state 数据模型，
所以这里**不做内容审核判定**，只做结构性校验，并预留 P1-2 的接入点。

明确禁止：**不得**使用 `KnowledgeDoc.is_sensitive` / `sensitive_checked`
作为"已通过安全审核"的依据——二者是全仓库无任何写入点的死字段，
把它们包装成安全机制只会制造虚假的安全感。
"""
import logging

logger = logging.getLogger(__name__)

# 允许进入索引流程的 OSS Key 前缀。
# 上传路径为 knowledge/{scope}/{owner_id}/…，爬虫路径为 knowledge/website/…，
# 两者都在该前缀下；前缀之外的 Key 不来自本系统的正常上传链路。
INDEXABLE_OSS_KEY_PREFIX = 'knowledge/'


def check_indexable(doc) -> tuple:
    """判断文档当前是否允许进入索引流程。

    Args:
        doc: KnowledgeDoc 实例

    Returns:
        (True, '')       允许索引
        (False, reason)  拒绝索引，reason 为可直接展示的原因说明

    Note:
        P1-2 的 ContentModeration 落地后，在下方标注的接入点内追加审核状态判断
        即可，无需改动任何调用方。
    """
    oss_key = (doc.oss_key or '').strip()
    if not oss_key:
        return False, '文档没有关联的 OSS 对象'
    if not oss_key.startswith(INDEXABLE_OSS_KEY_PREFIX):
        return False, 'OSS 对象不在知识库路径下'

    # --- P1-2 接入点：内容安全审核门 ---------------------------------
    # ContentModeration 模型落地后在此处启用，例如：
    #
    #     from .models import ContentModeration
    #     finding = ContentModeration.objects.filter(doc=doc).first()
    #     if finding is None or finding.state != 'approved':
    #         return False, '文档尚未通过安全审核'
    #
    # 在此之前不得以 is_sensitive / sensitive_checked 冒充审核结论。
    # ---------------------------------------------------------------

    return True, ''
