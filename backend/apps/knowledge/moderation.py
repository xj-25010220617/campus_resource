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

阶段边界
--------
P1-1：本模块只做结构性校验（oss_key 是否存在、是否位于知识库命名空间）。

P1-2A：已建立 ContentModeration / ModerationAttempt 数据模型与审核状态常量，
但**仍然不做任何内容审核判定**——`check_indexable()` 的语义保持 P1-1 原样，
现有生产数据与 P1-1 测试不受影响。真正的审核门要等上传流水线（P1-2D 及以后）
把审核记录可靠地绑到每一份文档之后，才能切换。

明确禁止：**不得**使用 `KnowledgeDoc.is_sensitive` / `sensitive_checked`
作为"已通过安全审核"的依据——二者是全仓库无任何写入点的死字段，
把它们包装成安全机制只会制造虚假的安全感。
"""
import logging
import time

logger = logging.getLogger(__name__)

# 允许进入索引流程的 OSS Key 前缀。
# 上传路径为 knowledge/{scope}/{owner_id}/…，爬虫路径为 knowledge/website/…，
# 两者都在该前缀下；前缀之外的 Key 不来自本系统的正常上传链路。
#
# P1-2 之后该前缀的含义会收紧为"已完成 promotion 的正式内容命名空间"，
# 隔离区改用平级的 quarantine/ 前缀（见 utils.oss.QUARANTINE_KEY_PREFIX）。
INDEXABLE_OSS_KEY_PREFIX = 'knowledge/'


# ---------------------------------------------------------------------------
# 审核状态 / 审核结论 / 存储状态
#
# 这里是 choices 的唯一来源，模型层直接引用这些常量，避免状态字符串散落各处。
# ---------------------------------------------------------------------------

# 审核流程状态
MODERATION_STATE_PENDING = 'pending'
MODERATION_STATE_SCANNING = 'scanning'
MODERATION_STATE_APPROVED = 'approved'
MODERATION_STATE_REJECTED = 'rejected'
MODERATION_STATE_UNAVAILABLE = 'unavailable'
MODERATION_STATE_CONFIRMED_UNCHECKED = 'confirmed_unchecked'
MODERATION_STATE_ERROR = 'error'

MODERATION_STATE_CHOICES = [
    (MODERATION_STATE_PENDING, '待审核'),
    (MODERATION_STATE_SCANNING, '审核中'),
    (MODERATION_STATE_APPROVED, '已通过'),
    (MODERATION_STATE_REJECTED, '已拒绝'),
    (MODERATION_STATE_UNAVAILABLE, '审核不可用'),
    (MODERATION_STATE_CONFIRMED_UNCHECKED, '人工确认放行'),
    (MODERATION_STATE_ERROR, '审核异常'),
]

# 审核结论（内容性质）。空字符串表示"尚未产生结论"，不属于任何一类。
VERDICT_CLEAN = 'clean'
VERDICT_VIRUS = 'virus'
VERDICT_SENSITIVE = 'sensitive'
VERDICT_HARMFUL = 'harmful'
VERDICT_NEGATIVE_GUIDANCE = 'negative_guidance'
VERDICT_MIXED = 'mixed'
VERDICT_UNAVAILABLE = 'unavailable'
VERDICT_ERROR = 'error'

VERDICT_CHOICES = [
    (VERDICT_CLEAN, '无风险'),
    (VERDICT_VIRUS, '病毒'),
    (VERDICT_SENSITIVE, '敏感'),
    (VERDICT_HARMFUL, '有害内容'),
    (VERDICT_NEGATIVE_GUIDANCE, '负面指导'),
    (VERDICT_MIXED, '多类风险'),
    (VERDICT_UNAVAILABLE, '审核不可用'),
    (VERDICT_ERROR, '审核异常'),
]

# 存储状态（对象在 OSS 命名空间中的位置）
STORAGE_STATE_QUARANTINE = 'quarantine'
STORAGE_STATE_PROMOTING = 'promoting'
STORAGE_STATE_PROMOTED = 'promoted'
STORAGE_STATE_PROMOTION_FAILED = 'promotion_failed'
STORAGE_STATE_DELETED = 'deleted'

STORAGE_STATE_CHOICES = [
    (STORAGE_STATE_QUARANTINE, '隔离区'),
    (STORAGE_STATE_PROMOTING, '提升中'),
    (STORAGE_STATE_PROMOTED, '已提升'),
    (STORAGE_STATE_PROMOTION_FAILED, '提升失败'),
    (STORAGE_STATE_DELETED, '已删除'),
]

# **唯一**允许放行的审核状态白名单。
#
# 必须使用白名单（`state in INDEXABLE_MODERATION_STATES`）判断，禁止写成
# `state != 'rejected'` 这类黑名单：黑名单在将来新增状态时会**静默放行**，
# 而白名单只会**静默拒绝**。同一个遗漏，后果完全相反。
INDEXABLE_MODERATION_STATES = frozenset({
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_CONFIRMED_UNCHECKED,
})

# ---------------------------------------------------------------------------
# findings 结构约束（P1-2A 只定义约束，不实现检测器）
#
# findings 是审核结论的证据列表，未来可能承载敏感原文片段，因此必须限长，
# 否则一份命中上千个敏感词的文档会把 JSON 字段撑爆，且把审核表变成第二份
# 敏感内容副本（见 security_moderation_phase1_2_design_audit.md §8）。
# ---------------------------------------------------------------------------

# 单条 finding 最多保留的脱敏片段长度（字符）
MAX_EXCERPT_LENGTH = 40
# 单份文档最多保留的 finding 条数
MAX_FINDINGS = 50
# findings 序列化后的字节上限（兜底）
MAX_FINDINGS_JSON_BYTES = 64 * 1024


def check_indexable(doc) -> tuple:
    """判断文档当前是否允许进入索引流程。

    Args:
        doc: KnowledgeDoc 实例

    Returns:
        (True, '')       允许索引
        (False, reason)  拒绝索引，reason 为可直接展示的原因说明

    Note:
        本函数是索引准入的**唯一**集中式入口，命名空间检查与审核门都在这里，
        调用方（`index_document_task`、脚本、未来的后台任务）只需相信其返回值。
        P1-2D STEP 4-B 起审核门已启用，判定委托给
        `is_document_indexable_by_moderation()`，改造调用方无需改动。
    """
    oss_key = (doc.oss_key or '').strip()
    if not oss_key:
        return False, '文档没有关联的 OSS 对象'
    if not oss_key.startswith(INDEXABLE_OSS_KEY_PREFIX):
        return False, 'OSS 对象不在知识库路径下'

    # --- P1-2D STEP 4-B：内容安全审核门（已启用） ----------------------
    # 索引准入必须同时满足三个条件，缺一不可：
    #
    #     1. 对象位于正式区（knowledge/）        —— 上面的前缀检查
    #     2. ContentModeration.storage_state == promoted  —— 已完成 promotion
    #     3. ContentModeration.state ∈ INDEXABLE_MODERATION_STATES（白名单）
    #
    # 复用 is_document_indexable_by_moderation()，不在此处复制判断逻辑，
    # 避免同一个安全语义出现第二份实现后各自漂移。
    #
    # ⚠️ 兼容性后果（STEP 4-B 报告 §9 已记录）：尚无 ContentModeration 记录的
    # 历史文档与爬虫文档会被判为不可索引。这是**故意**的 fail-closed ——
    # 绝不能为了兼容存量数据而让 `knowledge/` 前缀本身变成放行依据。
    return is_document_indexable_by_moderation(doc)


# ---------------------------------------------------------------------------
# P1-2A helpers（纯函数，不触碰数据库、不触碰 OSS）
# ---------------------------------------------------------------------------

def is_indexable_moderation_state(state) -> bool:
    """审核状态是否允许放行（白名单判断）。

    Args:
        state: 审核状态字符串

    Returns:
        True  仅当 state 属于 INDEXABLE_MODERATION_STATES
              （即 approved / confirmed_unchecked）

    Note:
        未知状态、空值、None 一律返回 False。判定必须走白名单，
        不要在外层改写成 `state != 'rejected'`。
    """
    return state in INDEXABLE_MODERATION_STATES


def normalize_findings(findings) -> list:
    """把 findings 裁剪到 P1-2A 约定的结构约束内。

    约束（见本模块常量与设计审计报告 §8）：
    - 条数不超过 MAX_FINDINGS
    - 单条 excerpt 不超过 MAX_EXCERPT_LENGTH 字符
    - 序列化后总字节数不超过 MAX_FINDINGS_JSON_BYTES（超出则继续丢弃末条）

    Args:
        findings: 任意可迭代的 finding 字典序列，允许为 None

    Returns:
        裁剪后的新列表（不修改入参；非字典元素被丢弃）

    Note:
        本函数**只做长度约束，不做脱敏**。把命中原文裁剪成不可识别形式
        （掩码、截断）是检测器/规则层的责任；本函数不是脱敏的替代品，
        调用方不得因为"反正会被截断"就写入未脱敏的全文。
    """
    import json

    if not findings:
        return []

    capped = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        excerpt = copied.get('excerpt')
        if isinstance(excerpt, str) and len(excerpt) > MAX_EXCERPT_LENGTH:
            copied['excerpt'] = excerpt[:MAX_EXCERPT_LENGTH] + '…'
        capped.append(copied)
        if len(capped) >= MAX_FINDINGS:
            break

    # 兜底：序列化后仍超限则持续丢弃末条，直到落回首字节预算内
    while capped and len(json.dumps(capped, ensure_ascii=False).encode('utf-8')) > MAX_FINDINGS_JSON_BYTES:
        capped.pop()

    return capped


# ===========================================================================
# P1-2C：审核策略 / 状态机 / 聚合器 / 统一审核服务
#
# 本段是 P1-2C 新增内容。上方 P1-2A 的常量与 check_indexable 语义**未被修改**。
# ===========================================================================


# ---------------------------------------------------------------------------
# 审核策略（§二十七 策略版本 / §二十六 required & optional detectors）
#
# 策略必须外置为配置，而不是写死在业务流程里：将来"某检测器可以缺席"这一
# 判断若散落在各个 if 里，任何一处漏改都会变成 fail-open。
# ---------------------------------------------------------------------------

def _policy_setting(name, default):
    from django.conf import settings
    return getattr(settings, name, default)


def policy_version() -> str:
    """当前审核策略版本，写入 ContentModeration / ModerationAttempt。"""
    return str(_policy_setting('CONTENT_MODERATION_POLICY_VERSION', 'content-moderation-v1'))


def required_detectors() -> tuple:
    """**必需**检测器：其中任何一个没能给出结论，本次审核就只能是 unavailable/error。

    默认三者缺一不可——审核的语义是"该查的都查过"，不是"查过一部分"。
    若某次部署确实要放宽（例如未部署 ClamAV），必须**显式**把它从必需列表里
    移到可选列表，这是有记录的、需要评审的决定，而不是让代码静默跳过。
    """
    return tuple(_policy_setting(
        'CONTENT_MODERATION_REQUIRED_DETECTORS', ('clamav', 'rule', 'llm'),
    ))


def optional_detectors() -> tuple:
    """**可选**检测器：缺席不阻断放行；但一旦给出非 clean 结论，依然计入判决。"""
    return tuple(_policy_setting(
        'CONTENT_MODERATION_OPTIONAL_DETECTORS', ('vision',),
    ))


def vision_required_for_images() -> bool:
    """图片是否必须由视觉检测器给出结论（默认必须）。

    图片没有任何可抽文本，若允许"视觉不可用也放行"，等于图片内容完全没被检查。
    """
    return bool(_policy_setting('CONTENT_MODERATION_VISION_REQUIRED_FOR_IMAGES', True))


def max_attempts() -> int:
    return int(_policy_setting('CONTENT_MODERATION_MAX_ATTEMPTS', 3))


def retry_backoff_seconds() -> int:
    return int(_policy_setting('CONTENT_MODERATION_RETRY_BACKOFF_SECONDS', 300))


def default_lease_seconds() -> int:
    return int(_policy_setting('CONTENT_MODERATION_LEASE_SECONDS', 600))


# ---------------------------------------------------------------------------
# 状态机（§四十六：集中定义，禁止在多个文件里写 `if state == "approved"`）
# ---------------------------------------------------------------------------

#: 终态：到达后不再自动重跑（`force=True` 除外）
TERMINAL_MODERATION_STATES = frozenset({
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_REJECTED,
    MODERATION_STATE_CONFIRMED_UNCHECKED,
})

#: **强拒绝**结论：出现即判定 rejected，且优先级最高
HARD_REJECT_VERDICTS = frozenset({
    VERDICT_VIRUS,
    VERDICT_HARMFUL,
    VERDICT_NEGATIVE_GUIDANCE,
    VERDICT_MIXED,
})

#: 非 clean 结论（用于冲突检测）
NON_CLEAN_VERDICTS = frozenset({
    VERDICT_SENSITIVE,
    VERDICT_HARMFUL,
    VERDICT_NEGATIVE_GUIDANCE,
    VERDICT_MIXED,
})

#: 允许的状态迁移。`pending → scanning → {approved|rejected|unavailable|error}`
#: 是主路径；其余为重新审核 / 人工干预路径。
#:
#: 本表是**唯一**的状态机定义，供 P1-2D / P1-2E 复用；P1-2C 不在业务热路径上
#: 强制校验它（那会把策略表变成运行时炸弹），只在测试中守护其完整性。
ALLOWED_TRANSITIONS = {
    MODERATION_STATE_PENDING: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_UNAVAILABLE,
        MODERATION_STATE_ERROR, MODERATION_STATE_APPROVED,
        MODERATION_STATE_REJECTED, MODERATION_STATE_CONFIRMED_UNCHECKED,
    }),
    MODERATION_STATE_SCANNING: frozenset({
        MODERATION_STATE_APPROVED, MODERATION_STATE_REJECTED,
        MODERATION_STATE_UNAVAILABLE, MODERATION_STATE_ERROR,
        MODERATION_STATE_PENDING,
    }),
    MODERATION_STATE_UNAVAILABLE: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_APPROVED,
        MODERATION_STATE_REJECTED, MODERATION_STATE_ERROR,
        MODERATION_STATE_CONFIRMED_UNCHECKED, MODERATION_STATE_PENDING,
    }),
    MODERATION_STATE_ERROR: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_APPROVED,
        MODERATION_STATE_REJECTED, MODERATION_STATE_UNAVAILABLE,
        MODERATION_STATE_PENDING,
    }),
    MODERATION_STATE_APPROVED: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_REJECTED,
        MODERATION_STATE_UNAVAILABLE, MODERATION_STATE_ERROR,
    }),
    MODERATION_STATE_REJECTED: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_UNAVAILABLE,
        MODERATION_STATE_ERROR,
    }),
    MODERATION_STATE_CONFIRMED_UNCHECKED: frozenset({
        MODERATION_STATE_SCANNING, MODERATION_STATE_REJECTED,
        MODERATION_STATE_UNAVAILABLE, MODERATION_STATE_ERROR,
    }),
}


def is_valid_transition(from_state: str, to_state: str) -> bool:
    """状态迁移是否合法。未知起点一律返回 False（白名单语义）。"""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


# ---------------------------------------------------------------------------
# 聚合器
# ---------------------------------------------------------------------------

class AggregationResult:
    """聚合输出（§二十三）。

    Attributes:
        state: ContentModeration.state 的目标值
        verdict: ContentModeration.verdict 的目标值
        categories: 命中的类别列表
        findings: **已脱敏且已限长**的证据列表
        failure_reason: 不可用 / 异常原因
        service: 产生主要结论的服务名
    """

    __slots__ = ('state', 'verdict', 'categories', 'findings',
                 'failure_reason', 'service')

    def __init__(self, state, verdict, categories=None, findings=None,
                 failure_reason='', service=''):
        self.state = state
        self.verdict = verdict
        self.categories = list(categories or [])
        self.findings = list(findings or [])
        self.failure_reason = failure_reason
        self.service = service

    def __repr__(self):
        return f'<AggregationResult {self.state}/{self.verdict} service={self.service!r}>'


def _reject_verdict(verdicts) -> str:
    """从非 clean 结论集合中选出最终结论。

    - 病毒优先级最高（确定性最强，且不需要再判断内容语义）
    - 存在 ≥2 类不同风险 → mixed
    - 只有一类 → 该类本身
    """
    if VERDICT_VIRUS in verdicts:
        return VERDICT_VIRUS
    distinct = {v for v in verdicts if v in NON_CLEAN_VERDICTS}
    if len(distinct) >= 2:
        return VERDICT_MIXED
    if len(distinct) == 1:
        return next(iter(distinct))
    return ''



def aggregate_results(extraction_result, clamav_result=None, rule_result=None,
                      llm_result=None, vision_result=None) -> AggregationResult:
    """把各检测器结论聚合为最终审核状态（§二十三 / §二十四 / §二十五 / §二十六）。

    判决顺序（顺序本身就是安全策略，不可调整）：

        1. 抽取层没能给出可用内容           → unavailable / error
        2. 任一检测器给出强拒绝结论         → rejected（**先于**"缺失结论"判断：
           已经确认是病毒时，其他检测器不可用不改变"必须拒绝"这一事实）
        3. 必需检测器缺结论                 → unavailable / error（**绝不 approved**）
        4. 全部给出 clean                   → approved

    Args:
        extraction_result: extraction.ExtractResult
        *其余: detectors.DetectorResult 或 None
    """
    from .detectors import (
        DETECTOR_ERROR, DETECTOR_OK, DETECTOR_SKIPPED, DETECTOR_UNAVAILABLE,
    )
    from .extraction import (
        EXTRACT_DEPENDENCY_MISSING, EXTRACT_EMPTY, EXTRACT_FAILED, EXTRACT_UNSUPPORTED,
    )

    detector_results = [r for r in (clamav_result, rule_result, llm_result, vision_result)
                        if r is not None]

    # 收集 findings / categories（无论最终状态如何，命中的证据都应保留下来）
    findings = []
    categories = set()
    for result in detector_results:
        findings.extend(result.findings)
        categories.update(result.categories)
    findings = normalize_findings(findings)

    # ---- 1. 抽取层 ----
    if not extraction_result.is_acceptable:
        reason = extraction_result.error or '内容抽取未成功'
        if extraction_result.status == EXTRACT_FAILED:
            return AggregationResult(
                MODERATION_STATE_ERROR, VERDICT_ERROR, categories, findings,
                failure_reason=reason, service='extraction',
            )
        # unsupported / empty / dependency_missing —— 全部是"没能审核"，不是"干净"
        return AggregationResult(
            MODERATION_STATE_UNAVAILABLE, VERDICT_UNAVAILABLE, categories, findings,
            failure_reason=reason, service='extraction',
        )

    # ---- 2. 强拒绝（先于缺失判断） ----
    verdicts = [r.verdict for r in detector_results if r.status == DETECTOR_OK and r.verdict]
    reject = _reject_verdict(verdicts)
    if reject in HARD_REJECT_VERDICTS:
        service = next(
            (r.name for r in detector_results
             if r.status == DETECTOR_OK and r.verdict in HARD_REJECT_VERDICTS),
            '',
        )
        return AggregationResult(
            MODERATION_STATE_REJECTED, reject, sorted(categories), findings,
            failure_reason='', service=service,
        )

    # ---- 3. 缺失结论 ----
    # 哪些检测器**必须**给出结论，取决于抽取出来的内容形态。
    # 这里的增删本身就是安全策略：把某个检测器从这个集合里拿掉，等于宣告
    # "这类内容不需要它来检查"，所以只在"它确实无法工作"时才可以拿掉。
    required = set(required_detectors())
    if extraction_result.is_text:
        # 纯文本没有图像内容，视觉检测器无从下手 → 不适用，不算缺席
        required.discard('vision')
    elif extraction_result.is_image:
        # 图片没有可抽文本，规则/模型没有输入，其缺席不是"漏检"；
        # 图片的检查由病毒扫描 + 视觉检测器承担。
        required.discard('rule')
        required.discard('llm')
        if vision_required_for_images():
            required.add('vision')

    unavailable = [r for r in detector_results
                   if r.status == DETECTOR_UNAVAILABLE and r.name in required]
    errored = [r for r in detector_results
               if r.status == DETECTOR_ERROR and r.name in required]
    # 被**编排层跳过**的必需检测器同样是"没检查过内容"。如果不单独拦下，
    # 它会绕过上面两个列表直接落到"全部干净"分支——那正是 fail-open。
    skipped_required = [r for r in detector_results
                        if r.status == DETECTOR_SKIPPED and r.name in required]

    # 必需检测器**根本没有出现在结果里**（配置写了但编排层没跑）也必须阻断：
    # 这是"策略要求检测但实际没检测"的最危险形态。
    present = {r.name for r in detector_results}
    missing = sorted(required - present)
    if missing:
        return AggregationResult(
            MODERATION_STATE_ERROR, VERDICT_ERROR, sorted(categories), findings,
            failure_reason=f'必需检测器未执行: {", ".join(missing)}', service='',
        )

    if unavailable:
        names = ', '.join(sorted({r.name for r in unavailable}))
        detail = '; '.join(
            r.error_message or r.reason for r in unavailable if (r.error_message or r.reason)
        )
        return AggregationResult(
            MODERATION_STATE_UNAVAILABLE, VERDICT_UNAVAILABLE, sorted(categories), findings,
            failure_reason=f'检测器不可用（{names}）: {detail}'[:500],
            service=unavailable[0].name,
        )

    if errored:
        names = ', '.join(sorted({r.name for r in errored}))
        detail = '; '.join(r.error_message or r.reason
                           for r in errored if (r.error_message or r.reason))
        return AggregationResult(
            MODERATION_STATE_ERROR, VERDICT_ERROR, sorted(categories), findings,
            failure_reason=f'检测器异常（{names}）: {detail}'[:500],
            service=errored[0].name,
        )

    if skipped_required:
        names = ', '.join(sorted({r.name for r in skipped_required}))
        return AggregationResult(
            MODERATION_STATE_UNAVAILABLE, VERDICT_UNAVAILABLE, sorted(categories), findings,
            failure_reason=f'必需检测器未执行内容检查（{names}）'[:500],
            service=skipped_required[0].name,
        )

    # ---- 4. 全部干净 ----
    # 走到这里说明：抽取可用，且所有必需检测器都给出了结论。
    # 若其中存在 sensitive 结论，它不是强拒绝，但也绝非 clean。
    if VERDICT_SENSITIVE in verdicts:
        return AggregationResult(
            MODERATION_STATE_REJECTED, VERDICT_SENSITIVE, sorted(categories), findings,
            failure_reason='', service='rule',
        )

    return AggregationResult(
        MODERATION_STATE_APPROVED, VERDICT_CLEAN, sorted(categories), findings,
        failure_reason='', service='aggregate',
    )


# ---------------------------------------------------------------------------
# 统一审核服务（§五 / §九 / §十 / §十一）
# ---------------------------------------------------------------------------

class ModerationRunResult:
    """一次 `moderate_document` 调用的结果（**不是**数据库记录）。"""

    __slots__ = ('doc_id', 'moderation_id', 'attempt_seq', 'state', 'verdict',
                 'applied', 'skipped_reason', 'duration_ms')

    def __init__(self, doc_id=None, moderation_id=None, attempt_seq=0, state='',
                 verdict='', applied=False, skipped_reason='', duration_ms=0):
        self.doc_id = doc_id
        self.moderation_id = moderation_id
        self.attempt_seq = attempt_seq
        self.state = state
        self.verdict = verdict
        self.applied = applied
        self.skipped_reason = skipped_reason
        self.duration_ms = duration_ms

    def __repr__(self):
        return (f'<ModerationRunResult doc={self.doc_id} attempt={self.attempt_seq} '
                f'{self.state}/{self.verdict} applied={self.applied} '
                f'skipped={self.skipped_reason!r}>')


def _run_detectors(oss_key, extract_result, max_bytes):
    """按抽取结果决定哪些检测器适用，并执行它们。

    返回 (clamav, rule, llm, vision) 四元组，未执行的为 DetectorResult.skipped。

    注意：**病毒命中会短路后续检测器**（§十一）。被短路跳过的检测器记为
    skipped 而非 unavailable——它们不是"失败"，只是没必要再跑。
    """
    from .detectors import (
        DETECTOR_OK, ClamAVAdapter, DetectorResult, LLMAdapter, RuleAdapter, VisionAdapter,
    )

    clamav = ClamAVAdapter().scan_oss_key(oss_key, max_bytes=max_bytes)

    if clamav.status == DETECTOR_OK and clamav.verdict == VERDICT_VIRUS:
        rule = DetectorResult.skipped('rule', reason='short_circuit_virus')
        llm = DetectorResult.skipped('llm', reason='short_circuit_virus')
        vision = DetectorResult.skipped('vision', reason='short_circuit_virus')
        return clamav, rule, llm, vision

    if extract_result.is_text:
        rule = RuleAdapter().check(extract_result.text)
        llm = LLMAdapter().check(extract_result.text)
        vision = DetectorResult.skipped('vision', reason='not_applicable_text')
    elif extract_result.is_image:
        # 图片没有文本：规则/模型无法工作，这是"不适用"而非"失败"。
        rule = DetectorResult.skipped('rule', reason='not_applicable_image')
        llm = DetectorResult.skipped('llm', reason='not_applicable_image')
        vision = VisionAdapter().check_image(oss_key)
    else:
        rule = DetectorResult.skipped('rule', reason='not_applicable')
        llm = DetectorResult.skipped('llm', reason='not_applicable')
        vision = DetectorResult.skipped('vision', reason='not_applicable')

    return clamav, rule, llm, vision


def _acquire_lease(moderation_id, owner, seconds):
    """抢占审核租约（CAS：只在没有有效租约时成功）。

    Returns:
        True 抢到租约；False 已有其他 worker 在审。
    """
    import datetime

    from django.db.models import Q
    from django.utils import timezone

    now = timezone.now()
    return bool(
        _ContentModerationModel().objects.filter(pk=moderation_id)
        .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
        .update(
            lease_owner=owner,
            lease_expires_at=now + datetime.timedelta(seconds=seconds),
        )
    )


def _release_lease(moderation_id, owner):
    """释放自己的租约（只释放自己的，绝不误清他人的）。"""
    _ContentModerationModel().objects.filter(
        pk=moderation_id, lease_owner=owner,
    ).update(lease_owner='', lease_expires_at=None)


def _ContentModerationModel():
    from .models import ContentModeration
    return ContentModeration


def _ModerationAttemptModel():
    from .models import ModerationAttempt
    return ModerationAttempt


def moderate_document(doc_id, oss_key=None, file_type=None, file_name=None,
                      force=False, worker_id='', lease_seconds=None):
    """统一内容安全审核入口（§五）。

    这是 upload pipeline / crawler / FAQ / RAG 未来**唯一**应该调用的审核函数。
    调用方不需要知道 ClamAV / 规则 / LLM / Vision / 抽取的存在。

    本函数**只写 ContentModeration 与 ModerationAttempt**：
    - 不修改 `KnowledgeDoc.status`
    - 不 promotion、不写 Milvus、不索引、不改存储位置
    （§三十三 / §四十八）

    Args:
        doc_id: KnowledgeDoc 主键
        oss_key: 要审核的 OSS 对象；不传则用 `doc.oss_key`
        file_type / file_name: 覆盖文档上的取值（扩展名判定用）
        force: 文档已处于终态时是否强制重审（默认 False，避免重复/重试风暴）
        worker_id: 执行者标识，写入租约与 ModerationAttempt
        lease_seconds: 租约时长，默认取策略配置

    Returns:
        ModerationRunResult。**注意看 `applied`**：为 False 表示本次结果未生效
        （被别人抢先、或已有更新的尝试）。
    """
    import datetime

    from django.db import transaction
    from django.db.models import F
    from django.utils import timezone

    from .extraction import extract_from_oss, max_download_bytes

    started = time.time()
    ContentModeration = _ContentModerationModel()
    ModerationAttempt = _ModerationAttemptModel()

    from .models import KnowledgeDoc

    def _skip(reason, moderation=None, attempt_seq=0):
        return ModerationRunResult(
            doc_id=doc_id,
            moderation_id=getattr(moderation, 'pk', None),
            attempt_seq=attempt_seq, skipped_reason=reason,
            duration_ms=int((time.time() - started) * 1000),
        )

    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.warning(f'审核服务：doc_id={doc_id} 不存在')
        return _skip('doc_not_found')

    key = (oss_key if oss_key is not None else doc.oss_key) or ''
    if not key:
        return _skip('no_oss_key')

    f_type = file_type if file_type is not None else doc.file_type
    f_name = file_name if file_name is not None else doc.file_name

    moderation, _created = ContentModeration.objects.get_or_create(doc=doc)

    if not force and moderation.state in TERMINAL_MODERATION_STATES:
        return _skip(f'already_{moderation.state}', moderation)

    # ---- 抢占租约 ----
    owner = worker_id or f'worker-{timezone.now().timestamp():.0f}'
    lease_secs = lease_seconds or default_lease_seconds()
    if not _acquire_lease(moderation.pk, owner, lease_secs):
        return _skip('lease_held', moderation)

    attempt = None
    # 必须在写入 scanning **之前**记录，否则 state_at_start 永远是 scanning
    state_at_start = moderation.state
    try:
        # 序号必须由数据库原子递增产生：并发的两个 worker 若各自读到同一个
        # attempt_count，就会生成重复 attempt_seq，而唯一约束会让后者直接 500。
        ContentModeration.objects.filter(pk=moderation.pk).update(
            attempt_count=F('attempt_count') + 1,
            policy_version=policy_version(),
        )
        moderation.refresh_from_db()
        attempt_seq = moderation.attempt_count

        # `scanning` 只是进度标记，但同样受 §三十 约束：**比已生效序号更旧**的
        # 尝试不得把它降级回 scanning。否则一次迟到的旧任务会把新的终态
        # （approved / rejected）覆盖成一个非终态，既丢失结论又让状态卡住。
        # 对正常推进的尝试 applied_seq 恒小于 attempt_seq，这个条件永远成立。
        ContentModeration.objects.filter(
            pk=moderation.pk, applied_seq__lt=attempt_seq,
        ).update(state=MODERATION_STATE_SCANNING)

        attempt = ModerationAttempt.objects.create(
            moderation=moderation,
            attempt_seq=attempt_seq,
            state_at_start=state_at_start,
            started_at=timezone.now(),
            policy_version=policy_version(),
            worker_id=owner,
        )

        # ---- 执行检测（网络/子进程调用，刻意放在事务之外） ----
        extract_result = extract_from_oss(key, f_type, f_name)
        clamav, rule, llm, vision = _run_detectors(
            key, extract_result, max_download_bytes(),
        )
        aggregation = aggregate_results(extract_result, clamav, rule, llm, vision)

        detector_results = [r.as_dict() for r in (clamav, rule, llm, vision)]

        # ---- 落库（§四十五：attempt 与 moderation 必须同事务） ----
        now = timezone.now()
        with transaction.atomic():
            attempt.finished_at = now
            attempt.verdict = aggregation.verdict
            attempt.detector_results = detector_results
            attempt.error = aggregation.failure_reason[:2000]
            attempt.policy_version = policy_version()
            attempt.save(update_fields=[
                'finished_at', 'verdict', 'detector_results', 'error', 'policy_version',
            ])

            # CAS：只有比当前已生效序号更"新"的尝试才允许写入。
            # 旧任务即使后完成，也绝不可能覆盖新结果（§三十）。
            next_retry = None
            if (aggregation.state in (MODERATION_STATE_UNAVAILABLE, MODERATION_STATE_ERROR)
                    and attempt_seq < max_attempts()):
                next_retry = now + datetime.timedelta(seconds=retry_backoff_seconds())

            applied = ContentModeration.objects.filter(
                pk=moderation.pk, applied_seq__lt=attempt_seq,
            ).update(
                state=aggregation.state,
                verdict=aggregation.verdict,
                categories=aggregation.categories,
                findings=aggregation.findings,
                checked_at=now,
                failure_reason=aggregation.failure_reason,
                service=aggregation.service,
                policy_version=policy_version(),
                applied_seq=attempt_seq,
                next_retry_at=next_retry,
            )

        moderation.refresh_from_db()
        logger.info(
            f'审核完成 doc_id={doc_id} attempt={attempt_seq} '
            f'state={aggregation.state} verdict={aggregation.verdict} '
            f'applied={bool(applied)}'
        )
        return ModerationRunResult(
            doc_id=doc_id, moderation_id=moderation.pk, attempt_seq=attempt_seq,
            state=aggregation.state, verdict=aggregation.verdict,
            applied=bool(applied),
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as e:
        # §三十二：异常也必须落成**状态**，不能只抛出去让调用方以为"没发生"。
        logger.exception(f'审核服务异常 doc_id={doc_id}: {e}')
        try:
            now = timezone.now()
            with transaction.atomic():
                if attempt is not None:
                    attempt.finished_at = now
                    attempt.verdict = VERDICT_ERROR
                    attempt.error = f'{type(e).__name__}: {e}'[:2000]
                    attempt.save(update_fields=['finished_at', 'verdict', 'error'])
                ContentModeration.objects.filter(
                    pk=moderation.pk, applied_seq__lt=attempt_seq,
                ).update(
                    state=MODERATION_STATE_ERROR,
                    verdict=VERDICT_ERROR,
                    checked_at=now,
                    failure_reason=f'审核服务异常: {type(e).__name__}'[:500],
                    service='moderation_service',
                    policy_version=policy_version(),
                    applied_seq=attempt_seq,
                )
        except Exception:
            logger.exception(f'审核服务：异常状态写入失败 doc_id={doc_id}')
        return ModerationRunResult(
            doc_id=doc_id, moderation_id=moderation.pk,
            attempt_seq=attempt_seq if attempt is not None else 0,
            state=MODERATION_STATE_ERROR, verdict=VERDICT_ERROR, applied=False,
            duration_ms=int((time.time() - started) * 1000),
        )
    finally:
        _release_lease(moderation.pk, owner)


def is_document_indexable_by_moderation(doc) -> tuple:
    """内容安全审核门：对象是否已完成审核 + promotion。

    返回 `(allowed, reason)`。STEP 4-B 起由 `check_indexable()` 调用，
    是索引准入的**唯一**审核判据（与命名空间检查合起来构成完整索引门）：

        storage_state == promoted
        AND moderation_state in INDEXABLE_MODERATION_STATES

    Note:
        记录不存在必须判为**拒绝**（`legacy_unchecked` 语义），绝不能因为
        "查不到记录"就默认放行——那会把全部历史文档默认当成已审核。
        该断言意味着**没有 ContentModeration 记录的历史文档与爬虫文档
        将无法被索引**，这是 STEP 4-B 有意选择的 fail-closed 方向。
    """
    ContentModeration = _ContentModerationModel()
    record = ContentModeration.objects.filter(doc=doc).first()
    if record is None:
        return False, '文档没有审核记录（历史文档需重新审核）'
    if not is_indexable_moderation_state(record.state):
        return False, f'文档未通过安全审核（当前状态：{record.state}）'
    if record.storage_state != STORAGE_STATE_PROMOTED:
        return False, f'文档尚未提升到正式区（当前存储状态：{record.storage_state}）'
    return True, ''


def filter_indexable_docs(qs):
    """把 KnowledgeDoc QuerySet 收窄到**通过审核门 + 存储门**的文档。

    这是 `is_document_indexable_by_moderation()` 的 queryset 形态，服务于
    **读取侧**（RAG 检索）的纵深防御第二层：

        写入侧 —— index_document_task 调 check_indexable()，逐文档判定
        读取侧 —— utils.rag 的三条检索路径调本函数，批量 SQL 过滤

    两侧共用 `INDEXABLE_MODERATION_STATES` 与 `STORAGE_STATE_PROMOTED`
    两个常量，**不存在第二份白名单**。`tests` 中有断言固定"queryset 版与
    单文档版对同一份数据给出相同结论"，防止两者将来各自漂移。

    语义（与单文档版逐条对应）：
        - 没有 ContentModeration 记录        → 不通过（历史文档 fail-closed）
        - state 不在白名单                    → 不通过
        - storage_state != promoted           → 不通过

    Args:
        qs: KnowledgeDoc 的 QuerySet

    Returns:
        收窄后的 QuerySet（用 Exists 子查询，不产生 N+1）
    """
    from django.db.models import Exists, OuterRef

    ContentModeration = _ContentModerationModel()
    gated = ContentModeration.objects.filter(
        doc_id=OuterRef('pk'),
        state__in=sorted(INDEXABLE_MODERATION_STATES),
        storage_state=STORAGE_STATE_PROMOTED,
    )
    return qs.filter(Exists(gated))
