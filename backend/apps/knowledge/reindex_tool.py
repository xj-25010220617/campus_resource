"""运维重索引工具（P1-2 Phase 1-2 / 审计报告 P-1、P-9）。

为什么需要本模块
================

改造前，运维脚本 `scripts/reindex_all.bat` 与 `scripts/index_all_docs.py`
直接做三件危险的事：

    DocChunk.objects.all().delete()          # 清空全部分块
    KnowledgeDoc.objects.all().update(status='uploaded')   # 批量重置状态
    rmdir /s /q chroma_db                    # 删掉整个向量库目录

三件事都**先破坏、后重建**，且完全不经过审核门：

- 一旦中途失败（Embedding 配额用尽、Milvus 连不上、进程被 Ctrl+C），
  知识库就停在一个"没有分块、状态全是 uploaded、向量库被清空"的半毁状态，
  而原始对象在 OSS 上还在——没有任何自动恢复路径。
- `status` 被批量重置为 `uploaded`，把"这份文档曾经索引成功过"的事实抹掉了。
  §5.4 明确禁止对 `indexed` 文档做批量 status 重置。
- 编辑 `index_all_docs.py` 时只挑 `status='uploaded'` 的文档，导致"重置过一次
  就再也回不到 indexed"的文档被**静默跳过**，脚本却报"完成"。

本模块把这套流程重写成**只读预检 + 逐文档重建**：

    预检（纯读）→ 逐个文档走 check_indexable() → 只处理通过门禁的文档
                                              → 派发/执行 index_document_task

`index_document_task` 内部**只删自己那一个 doc 的分块与向量**
（`milvus_client.delete_by_doc(doc_id)` + `DocChunk.objects.filter(doc_id=…)`），
因此不存在"先把整个库清空"的时间窗口，任何一份文档失败都不影响其他文档。

安全不变量（不要放宽）
======================

1. **绝不批量删除**：本模块没有任何 `objects.all().delete()` / `.update()`。
2. **绝不绕过审核门**：每个文档是否可索引，一律问 `check_indexable()`——
   它内部委托 `is_document_indexable_by_moderation()`，要求对象在
   `knowledge/`、`storage_state=promoted`、`state ∈ {approved, confirmed_unchecked}`。
   本模块**不**复制这套判定，也不提供任何"忽略门禁"的参数。
3. **`--force` 不是"绕过"**：它只允许重新索引**已经 `indexed`** 的文档
   （默认模式下这些文档会被跳过）。门禁依旧逐条生效，未审核的历史文档
   在任何模式下都不会被写入或提升。
4. **`--dry-run` 纯只读**：不写数据库、不碰 OSS、不碰 Milvus、不删分块。
   它调用 `check_indexable()`（纯函数，只读 `doc` 实例与关联的审核记录），
   除此之外不产生任何副作用。
5. 预检结果集非空但**全部不可索引**时，非 `--dry-run` 模式直接退出，
   不做任何写入——这正是当前生产库（95 份历史文档全部无审核记录）的表现。

使用（运维入口见 `scripts/reindex_all.bat` / management command）::

    python manage.py reindex_docs --dry-run            # 只看统计，不动任何数据
    python manage.py reindex_docs                      # 预检后重建可索引文档
    python manage.py reindex_docs --force --limit 50   # 连已 indexed 的也重建
"""

import logging

logger = logging.getLogger(__name__)


# 文档状态分组：仅用于 dry-run 统计展示，不参与任何判定。
_INDEXED = 'indexed'
_UPLOADED = 'uploaded'
_FAILED = 'failed'


class ReindexBlocked(RuntimeError):
    """预检未通过，拒绝执行任何写入（fail-closed）。"""


def _docs_qs(statuses=None):
    """取候选文档 QuerySet（只读，不 order_by 之外的任何修改）。"""
    from .models import KnowledgeDoc

    qs = KnowledgeDoc.objects.all()
    if statuses:
        qs = qs.filter(status__in=list(statuses))
    return qs.order_by('id')


def collect_stats() -> dict:
    """收集知识库现状统计。**纯只读**，dry-run 的全部数据来源。

    Returns:
        dict，键与含义见下方 `format_stats()`。统计口径刻意把"有审核记录 /
        无审核记录"与"可索引 / 不可索引"分开列——这两组数字的差值就是
        待人工处理的存量（当前生产库为 95 份无审核记录的历史文档）。
    """
    from .models import ContentModeration
    from .moderation import (
        INDEXABLE_MODERATION_STATES,
        MODERATION_STATE_APPROVED,
        MODERATION_STATE_CONFIRMED_UNCHECKED,
        STORAGE_STATE_PROMOTED,
        check_indexable,
    )

    total = 0
    by_status = {}
    has_moderation = 0
    approved = 0
    confirmed_unchecked = 0
    promoted = 0
    indexable = 0
    blocked_reasons = {}

    moderation_by_doc = {
        m.doc_id: m
        for m in ContentModeration.objects.all()
    }

    for doc in _docs_qs().iterator():
        total += 1
        by_status[doc.status] = by_status.get(doc.status, 0) + 1

        moderation = moderation_by_doc.get(doc.id)
        if moderation is not None:
            has_moderation += 1
            if moderation.state == MODERATION_STATE_APPROVED:
                approved += 1
            elif moderation.state == MODERATION_STATE_CONFIRMED_UNCHECKED:
                confirmed_unchecked += 1
            if moderation.storage_state == STORAGE_STATE_PROMOTED:
                promoted += 1

        allowed, reason = check_indexable(doc)
        if allowed:
            indexable += 1
        else:
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1

    return {
        'total': total,
        'by_status': by_status,
        'indexed': by_status.get(_INDEXED, 0),
        'uploaded': by_status.get(_UPLOADED, 0),
        'failed': by_status.get(_FAILED, 0),
        'has_moderation': has_moderation,
        'no_moderation': total - has_moderation,
        'approved': approved,
        'confirmed_unchecked': confirmed_unchecked,
        'promoted': promoted,
        'not_promoted': has_moderation - promoted,
        'indexable': indexable,
        'not_indexable': total - indexable,
        'blocked_reasons': blocked_reasons,
        # 白名单口径的自我描述：这两个常量是"可放行"的唯一定义
        'indexable_states': sorted(INDEXABLE_MODERATION_STATES),
    }


def format_stats(stats: dict) -> str:
    """把 `collect_stats()` 的结果渲染成可读文本（写入日志/标准输出）。"""
    lines = [
        '知识库重索引 — 现状统计（只读）',
        '-' * 46,
        f'KnowledgeDoc 总数          : {stats["total"]}',
        f'  status=indexed           : {stats["indexed"]}',
        f'  status=uploaded          : {stats["uploaded"]}',
        f'  status=failed            : {stats["failed"]}',
        f'  其他状态                 : '
        f'{stats["total"] - stats["indexed"] - stats["uploaded"] - stats["failed"]}',
        f'有 ContentModeration 记录  : {stats["has_moderation"]}',
        f'无 ContentModeration 记录  : {stats["no_moderation"]}',
        f'  state=approved           : {stats["approved"]}',
        f'  state=confirmed_unchecked: {stats["confirmed_unchecked"]}',
        f'storage_state=promoted     : {stats["promoted"]}',
        f'storage_state≠promoted     : {stats["not_promoted"]}',
        f'可索引（通过审核门）       : {stats["indexable"]}',
        f'不可索引                   : {stats["not_indexable"]}',
    ]
    if stats['blocked_reasons']:
        lines.append('不可索引原因分布：')
        for reason, count in sorted(
            stats['blocked_reasons'].items(), key=lambda kv: -kv[1],
        ):
            lines.append(f'  [{count:>4}] {reason}')
    lines.append(
        '放行状态白名单              : '
        + ', '.join(stats['indexable_states'])
    )
    return '\n'.join(lines)


def collect_preflight(include_indexed: bool):
    """逐个文档做索引准入判定（**纯只读**）。

    Args:
        include_indexed: True 时把已 `indexed` 的文档也纳入"待处理"，
            对应 `--force`；False 时跳过它们（默认模式）。**无论哪种取值，
            判定本身都是 `check_indexable()`**——本参数只影响"要不要重做
            已经做过的文档"，不影响"能不能做"。

    Returns:
        (eligible, blocked)
            eligible: [(doc, '')]                    可索引，待处理
            blocked : [(doc, reason)]                不可索引，**绝不处理**
    """
    from .moderation import check_indexable

    eligible, blocked = [], []
    for doc in _docs_qs().iterator():
        allowed, reason = check_indexable(doc)
        if not allowed:
            blocked.append((doc, reason))
            continue
        if doc.status == _INDEXED and not include_indexed:
            # 已经索引完成且未要求 --force：跳过，不算失败。
            continue
        eligible.append((doc, ''))
    return eligible, blocked


def run_reindex(
    *,
    dry_run: bool = True,
    force: bool = False,
    limit=None,
    dispatch=None,
    out=None,
) -> dict:
    """执行（或仅演练）重索引。

    Args:
        dry_run: True 只打印统计，**不产生任何写入**
        force: 是否也重建已经 `indexed` 的文档；**不解除审核门**
        limit: 最多处理多少份（None = 全部）
        dispatch: 处理单个文档的可调用对象，签名 `dispatch(doc) -> None`。
            默认派发 `index_document_task`；测试可注入替身。
        out: 日志输出函数，默认 `print`

    Returns:
        dict(dry_run, stats, eligible, blocked, processed, skipped)

    Raises:
        ReindexBlocked: 预检存在不可索引文档，且**不是** dry-run/force。
            默认模式在这里直接退出，绝不"跳过不合格的继续删"。
    """
    emit = out or print

    stats = collect_stats()
    emit(format_stats(stats))

    eligible, blocked = collect_preflight(include_indexed=force)

    result = {
        'dry_run': dry_run,
        'stats': stats,
        'eligible': len(eligible),
        'blocked': len(blocked),
        'processed': 0,
        'skipped': len(blocked),
    }

    emit('-' * 46)
    emit(f'可处理文档: {len(eligible)}    不可索引（绝不会被改动）: {len(blocked)}')

    if dry_run:
        emit('[dry-run] 未产生任何写入：没有删除分块、没有改动文档状态、'
             '没有访问 OSS / Milvus。')
        return result

    if blocked and not force:
        # fail-closed：库里还有没过门的文档时，默认模式一概不动。
        # 这是故意的——免得"重索引"被当成"顺便把历史数据也处理了"的借口。
        raise ReindexBlocked(
            f'预检未通过：{len(blocked)} 份文档不可索引（最常见原因：'
            f'没有 ContentModeration 记录或 storage_state≠promoted）。'
            f'默认模式拒绝执行任何写入。请先完成人工审核/确认'
            f'（manage.py confirm_moderation），或使用 --dry-run 查看统计。'
        )

    if limit is not None:
        eligible = eligible[:limit]

    if dispatch is None:
        dispatch = _default_dispatch()

    for doc, _ in eligible:
        dispatch(doc)
        result['processed'] += 1

    emit(f'已处理 {result["processed"]} 份（逐份重建，未做任何批量删除/重置）')
    if blocked:
        emit(f'跳过 {len(blocked)} 份不可索引文档（未修改其中任何一条记录）')
    return result


def _default_dispatch():
    """默认处理方式：派发 `index_document_task`（异步优先，失败降级同步）。

    这里**允许** `safe_delay` 的同步降级：本函数运行在运维脚本进程里，
    不在 HTTP 请求线程内，不存在撞穿 nginx 超时的问题；而 `safe_delay`
    在 Celery 不可用时同步跑完，反而让运维脚本在没有 worker 的环境下
    依然可用。真正的安全判定在任务体内的 `check_indexable()`，两条路径
    都绕不过去。
    """
    from utils.celery_dispatcher import safe_delay

    from .tasks import index_document_task

    def _dispatch(doc):
        logger.info('重索引派发 doc_id=%s status=%s', doc.id, doc.status)
        safe_delay(index_document_task, doc.id)

    return _dispatch
