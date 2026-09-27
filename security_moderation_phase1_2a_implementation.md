# Phase 1-2A 内容审核数据模型 + Quarantine OSS 基础设施 — 实施报告

> **报告时间**：2026-09-10
> **报告位置**：`E:\github\campus_resource\`（仓库根目录，**未纳入 git，请勿提交**）
> **工作目录**：`e:\github\campus_resource\backend`
> **阶段定位**：仅建立基础设施。**未实现任何真实检测器，未改动上传流水线，未启用放行门禁。**

---

## 0. 基线信息（精确取值）

```
BASELINE: 174a64889643a09ebbb4e23d605ae4b8f131ac24
BRANCH: phase-1-1-security-stopgap
ORIGIN_MAIN: 8c2d0e3e7fb4fad87e4ffb2b45055c880c8e91df
DATABASE_SCHEMA: CHANGED
CONTENT_MODERATION: CREATED
MODERATION_ATTEMPT: CREATED
MIGRATION: backend/apps/knowledge/migrations/0008_contentmoderation_moderationattempt_and_more.py
OSS_COPY: ADDED
OSS_STREAM: ADDED
OSS_NETWORK_ACCESS: 0
KNOWLEDGE_TESTS: 116 tests / OK
FULL_TESTS: 148 tests / 19 failed (all pre-existing, apps.accounts only)
PRODUCTION_CONNECTION: NO
UNRELATED_FILES: 1（见 §6.1，P1-1 既有测试类的 OSS 删除调用被 mock 掉）
```

- **分支 / HEAD 与规范要求一致**：当前分支即 `phase-1-1-security-stopgap`，HEAD 即 `174a6488…`，**未切换分支、未 commit、未 push、未 add**。
- **`origin/main` 未被触碰**：仍为 `8c2d0e3e…`。
- **报告文件本身不在 git 中**：与 `security_moderation_phase0_audit.md` 等历史报告同样为 untracked，符合"报告不得提交"的要求。

---

## 1. 本轮范围（严格对照规范 §4）

**做的**：

| 项 | 落点 |
|----|------|
| A. `ContentModeration` 模型 | [apps/knowledge/models.py](backend/apps/knowledge/models.py#L281) |
| B. `ModerationAttempt` 模型 | [apps/knowledge/models.py](backend/apps/knowledge/models.py#L443) |
| C. migration 0008 + migration 相关测试 | [apps/knowledge/migrations/0008_contentmoderation_moderationattempt_and_more.py](backend/apps/knowledge/migrations/0008_contentmoderation_moderationattempt_and_more.py) |
| D. OSS quarantine / promotion 基础原语 | [utils/oss.py](backend/utils/oss.py#L72) |
| 状态 / 结论 / 存储常量与白名单 helper | [apps/knowledge/moderation.py](backend/apps/knowledge/moderation.py#L51) |
| 单元测试 59 项 | [apps/knowledge/tests.py](backend/apps/knowledge/tests.py#L1113) |
| 变更记录 | [code-changes/2026-09-10.md](code-changes/2026-09-10.md)、[database-changes/content_moderation.md](database-changes/content_moderation.md)、[database-changes/moderation_attempt.md](database-changes/moderation_attempt.md) |

**明确没做的**（规范红线，逐条确认）：无 ClamAV / 无 LLM / 无 Vision / 无 Rule moderation；未改上传流水线；未启用 access moderation gate；未为历史文档回填任何审核记录；未改 `download` / `preview` / RAG / citation / FAQ / crawler / `data_query`；未改 `settings.py` / `requirements.txt` / `deploy/*`。

**已改动文件全集**（`git diff --name-only`）：

```
backend/apps/knowledge/models.py
backend/apps/knowledge/moderation.py
backend/apps/knowledge/tests.py
backend/utils/oss.py
code-changes/2026-09-10.md
```

**新增未跟踪文件**：`backend/apps/knowledge/migrations/0008_*.py`、`database-changes/content_moderation.md`、`database-changes/moderation_attempt.md`。**无任何规范 §39 禁止文件。**

---

## 2. MODEL CHANGES

### 2.1 `ContentModeration`（`db_table = 'content_moderation'`）

`OneToOneField` 挂到 `KnowledgeDoc`（`on_delete=CASCADE`，`related_name='moderation'`），唯一约束 `uk_cm_doc`。**没有把审核字段塞进 `knowledge_doc`** —— 该表已有 `is_sensitive` / `sensitive_checked` 两个语义不清的遗留字段，混入会让"审核状态"再次失去单一真源。

| 字段 | 类型 | 作用 |
|------|------|------|
| `state` | CharField(24)，default `pending`，db_index | 审核流程状态 |
| `verdict` | CharField(24)，default `''`，db_index | 内容性质结论；**空串 = 尚未产生结论**，不属于任何一类 |
| `categories` / `findings` | JSONField(default=list) | 结论类别 / 审核证据 |
| `checked_at` / `failure_reason` / `service` / `policy_version` | — | 终态时间、不可用原因、主要服务、策略版本 |
| `applied_seq` | PositiveIntegerField(0) | 已生效尝试序号，**CAS 依据** |
| `storage_state` | CharField(20)，default `quarantine`，db_index | 对象所在命名空间 |
| `quarantine_key` / `promoted_key` | CharField(512) | 隔离区 / 正式区对象 Key |
| `promotion_error` / `promotion_retry` | — | promotion 失败原因与重试计数 |
| `lease_owner` / `lease_expires_at` | — | 审核任务租约，用于僵死 worker 恢复 |
| `attempt_count` / `next_retry_at` | — | 有界重试控制（`next_retry_at` db_index） |
| `confirmed_by` / `confirmed_at` / `confirmation_reason` | FK→`AUTH_USER_MODEL`(SET_NULL) + 时间 + 文本 | 人工放行三要素 |
| `created_at` / `updated_at` | DateTimeField | 标准时间戳 |

索引：`idx_cm_state_storage (state, storage_state)` 复合索引，供"待审核队列 + 待 promotion 队列"扫描。
`ordering = ['-created_at']`。

两个 property：

- `is_approved` → 只调 `is_indexable_moderation_state(self.state)`。**只判审核状态，不判存储状态**——避免把"已复制到 `knowledge/`"误当成"已通过审核"。
- `is_promoted` → `storage_state == 'promoted'`。与放行判断**完全解耦**。

### 2.2 `ModerationAttempt`（`db_table = 'moderation_attempt'`）

`ForeignKey` → `ContentModeration`（CASCADE，`related_name='attempts'`），**不可变历史**。`content_moderation` 只保存"当前生效状态"，会被后续尝试覆盖；本表回答"第几次、何时、哪个 worker、什么策略、哪些检测器、什么结论、为什么失败"。核心约束：

```python
UniqueConstraint(fields=['moderation', 'attempt_seq'], name='uk_attempt_moderation_seq')
```

| 字段 | 作用 |
|------|------|
| `attempt_seq` | 尝试序号，从 1 递增；与 `moderation` 组成唯一约束 |
| `state_at_start` | 本次尝试开始时的审核状态 |
| `started_at` / `finished_at` | 起止时间 |
| `detector_results` | 各检测器原始结论（JSON）——**须先脱敏再落库** |
| `verdict` / `error` / `policy_version` / `worker_id` | 本次结论 / 异常 / 策略版本 / worker 标识 |

`ordering = ['-created_at']`。

### 2.3 常量与白名单 helper（`apps/knowledge/moderation.py`）

严格按规范 §6 给定取值，**没有增加任何额外状态**：

- `MODERATION_STATE_{PENDING,SCANNING,APPROVED,REJECTED,UNAVAILABLE,CONFIRMED_UNCHECKED,ERROR}`（7 个）+ `MODERATION_STATE_CHOICES`
- `VERDICT_{CLEAN,VIRUS,SENSITIVE,HARMFUL,NEGATIVE_GUIDANCE,MIXED,UNAVAILABLE,ERROR}`（8 个）+ `VERDICT_CHOICES`
- `STORAGE_STATE_{QUARANTINE,PROMOTING,PROMOTED,PROMOTION_FAILED,DELETED}`（5 个）+ `STORAGE_STATE_CHOICES`
- `INDEXABLE_MODERATION_STATES = frozenset({APPROVED, CONFIRMED_UNCHECKED})`
- `MAX_FINDINGS = 50`、`MAX_EXCERPT_LENGTH = 40`、`MAX_FINDINGS_JSON_BYTES = 64 * 1024`
- `is_indexable_moderation_state(state)`：`return state in INDEXABLE_MODERATION_STATES`
- `normalize_findings(findings)`：截断到 50 条 / `excerpt` ≤ 40 字符 / JSON ≤ 64KB；**只做长度约束，不做脱敏**

---

## 3. MIGRATION

`0008_contentmoderation_moderationattempt_and_more`，依赖 `0007_knowledgedoc_index_diagnostics` 与 `swappable_dependency(AUTH_USER_MODEL)`。

操作清单（**纯 schema**）：

```
CreateModel  ContentModeration
CreateModel  ModerationAttempt
AddIndex     idx_cm_state_storage
AddConstraint uk_attempt_moderation_seq
```

规范 §16 逐条核对：**无** `RunPython`；**无**数据迁移；**不扫描 OSS**；**不调 LLM / ClamAV / Vision**；**不创建任何 moderation record**；**不改历史文档状态**；**不改 `knowledge_doc` 任何字段**。

`manage.py makemigrations knowledge --check --dry-run` → `No changes detected in app 'knowledge'`（exit 0）。

---

## 4. OSS CHANGES

全部新增于 [utils/oss.py](backend/utils/oss.py)，**0 行删除**，未触碰任何既有函数。

新增常量：

```python
QUARANTINE_KEY_PREFIX = 'quarantine/'
OFFICIAL_KEY_PREFIX   = 'knowledge/'
DEFAULT_STREAM_CHUNK_SIZE  = 64 * 1024
DEFAULT_MAX_STREAM_BYTES   = 500 * 1024 * 1024
```

**`quarantine/` 与 `knowledge/` 平级**（规范 §18），**不是** `knowledge/quarantine/`。`OFFICIAL_KEY_PREFIX` 必须等于 `apps.knowledge.moderation.INDEXABLE_OSS_KEY_PREFIX`，由 `ModerationStateInvariantTests.test_official_prefix_matches_the_index_gate_prefix` 守住。

| 新增符号 | 行 | 行为 |
|----------|----|------|
| `build_quarantine_key(scope, owner_id, file_name)` | [84](backend/utils/oss.py#L84) | 生成 `quarantine/{scope}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}`。**刻意不复用** `_generate_oss_key`，以免扰动 P1-1 |
| `OSSOperationError` | [111](backend/utils/oss.py#L111) | 安全原语的专用异常。**刻意偏离**本模块"失败返回 `None`"的既有风格——静默降级在这些原语上是危险的 |
| `OSSClient._is_safe_key(key)` | [363](backend/utils/oss.py#L363) | 拒绝空 / 非 `str` / 前导 `/` / 含 `..` |
| `OSSClient.copy_object(src, dst, content_type=None)` | [371](backend/utils/oss.py#L371) | 复制 + **复制后 `head_object(dst)` 验证**；返回 `{src_key, dst_key, size, etag, content_type}`。传 `content_type` 时带 `x-oss-metadata-directive: REPLACE`（否则 OSS 默认 `COPY`，Content-Type 改不掉） |
| `OSSClient.iter_object_chunks(oss_key, chunk_size=64K, max_bytes=500M)` | [421](backend/utils/oss.py#L421) | 生成器，**流式分块**，绝不 `resp.content` 整文件读入内存；超 `max_bytes` **抛异常而非静默截断**；`finally: resp.close()` |
| `OSSClient.promote_object(src, dst, content_type=None)` | [481](backend/utils/oss.py#L481) | 前缀硬校验（`quarantine/` → `knowledge/`）+ head 源 + `copy_object` + **size 一致性比对**，返回 `{..., src_size, verified: True}`。**不删源、不碰数据库** |

规范 §25 的顺序已落实：`copy_object` → `head_object(dst)` → 校验 size → 才算成功；**不存在** `copy_object` 直接返回成功即认为成功的路径。

---

## 5. SECURITY INVARIANTS

1. **白名单放行**（§7）：放行判断唯一入口是 `is_indexable_moderation_state()`，实现 `state in INDEXABLE_MODERATION_STATES`。**没有**任何 `state != 'rejected'` 形式的黑名单判断——黑名单在将来新增状态时会静默放行。
2. **`confirmed_unchecked` 不会自动产生**（§8）：本轮**没有**任何代码路径写入该状态；没有实现任何自动确认 / 超时确认 / 定时确认。表级文档明确"`confirmed_by` + `confirmed_at` + 非空 `confirmation_reason` 三者必须同时具备，禁止任何定时任务或超时逻辑写入"。
3. **`unavailable` ≠ `approved`**：检测链未完成时停在 `unavailable`，绝不因"扫描失败"当干净放行。
4. **`oss_key` 语义未被改变**（§9）：`KnowledgeDoc.oss_key` 未改动、未迁移。模型 docstring 与表文档均明确写入"在 P1-2D 上传流水线完成之前，**不得**把 `KnowledgeDoc.oss_key` 的存在解释为'已经过审核'"。
5. **promotion 不隐式删除源对象**（§26）：`promote_object` 内**没有** `delete_object` 调用，`test_promote_never_deletes_the_source` 断言 `bucket.deleted_keys == []`。
6. **promotion 不改数据库**：`test_promote_does_not_touch_the_database` 断言调用前后 `ContentModeration` 计数不变。
7. **流式读取有硬上限**（§21）：`max_bytes` 超限抛 `OSSOperationError`，不吞异常、不返回 `None`。
8. **migration 无副作用**（§16）：纯 schema，无 `RunPython`。
9. **空文件策略不变**：本轮未触碰 `EMPTY_FILE_POLICY`，P1-1 的 `ALLOWED` 结论保持。
10. **`check_indexable()` 逐字节未变**：与 HEAD 版本做 EOL 归一化比较，结果 `True`；P1-1 的索引前置校验语义完全保持。

---

## 6. TESTS

### 6.1 新增测试（59 项，全部 mock OSS）

| 测试类 | 数量 | 覆盖 |
|--------|------|------|
| `ContentModerationModelTests` | 8 | 默认值、1:1 唯一性、`is_approved` / `is_promoted` 解耦、`test_oss_key_semantics_are_not_implicitly_approved` |
| `ModerationAttemptModelTests` | 5 | 唯一约束 `(moderation, attempt_seq)` 触发 `IntegrityError`、级联删除、排序 |
| `ModerationSchemaMigrationTests` | 4 | 表 / 列 / 约束 / 索引在 SQLite 测试库中确实存在 |
| `ModerationStateInvariantTests` | 8 | 白名单成员、7 种非放行状态全部 `False`、未知状态 / `None` / `''` 返回 `False`、两个前缀常量一致 |
| `QuarantineKeyTests` | 5 | Key 格式、月份分片、`quarantine/` 与 `knowledge/` 平级且**不嵌套** |
| `FindingsConstraintTests` | 7 | 50 条上限、40 字符 `excerpt` 截断、64KB 总字节上限、非 dict 项丢弃、空值处理 |
| `OSSCopyObjectTests` | 7 | 成功路径、`head_object` 验证、size 不符、copy 静默失败、不安全 key 拒绝 |
| `OSSStreamReadTests` | 7 | 分块顺序与大小、`max_bytes` 超限抛异常、`close()` 必被调用、参数校验 |
| `OSSPromotionTests` | 8 | 前缀双向拒绝、size 不一致拒绝、**不删源**、**不碰数据库**、返回 `verified: True` |

OSS 隔离方式：内存假桶 `_FakeOSSBucket`（含 `copy_transform` 开关，可模拟"复制静默失败"与"字节被改动导致 size 不符"两种故障），通过 `patch.object(oss_client, '_bucket', fake)` 注入。**无任何真实网络出口。**

### 6.2 未跟踪文件的测试结果

```
manage.py check --settings=config.settings_test          → System check identified no issues (0 silenced).
manage.py makemigrations knowledge --check --dry-run     → No changes detected in app 'knowledge'  (exit 0)

manage.py test apps.knowledge --settings=config.settings_test
  → Ran 116 tests in 17.111s — OK        aliyuncs=0

manage.py test --settings=config.settings_test
  → Ran 148 tests in 19.303s — FAILED (failures=15, errors=4)
  → 全部 19 项位于 apps.accounts（SsoMockTests / LoginApiTests / EdgeCaseTests）
  → aliyuncs=0
```

- **`aliyuncs` 出现次数 = 0**（§13 / §28 要求目标 0），知识库测试与全量测试两份日志均为 0。
- **全量 19 项失败与 P1-1 报告的既知基线完全一致**，本轮新增的 59 项测试无一失败；`apps.accounts` 之外的失败数为 0。
- 测试规模 89 → 148（+59）。

### 6.3 ⚠️ 偏差披露：`AdminScopeVisibilityTests` 的 OSS 删除调用被 mock

**这是本报告唯一的 `UNRELATED_FILES` 条目，也是本次判定 `PASS WITH WARNINGS` 的唯一原因。**

- **现象**：本轮首次运行 `apps.knowledge` 测试时，日志出现真实生产端点访问：
  `OSS 文件删除失败 oss_key=knowledge/college/1/2026-09/private.pdf: {'status': -2, ... 'details': "RequestError: HTTPSConnectionPool(host='campus-resourse.oss-cn-chengdu.aliyuncs.com', port=443)..."}`
- **归因**：命中路径为 P1-1 既有测试 `AdminScopeVisibilityTests.test_owner_can_delete_own_private_doc` → `KnowledgeDocDeleteView` → `views.py:312 oss_client.delete(instance.oss_key)`。`git diff -U0` 证明该测试类在本轮之前**就是 HEAD 的原样内容**（tests.py 仅 2 个 hunk、仅 2 行删除，且都是 import 行）。
- **为何之前可能未暴露**：`delete()` 在 bucket 为空时静默返回 `False` 不记日志；只有当 `.env` 中存在 AK、bucket 构造成功、真正走到网络层时才把端点写进日志。
- **处置**：在该类 `setUp` 中 `patch.object(oss_client, 'delete', return_value=True)` 并 `addCleanup` 停止，附带注释说明原因。此后 `aliyuncs = 0`。
- **性质判定**：这是**测试隔离缺陷的修复**，不是 P1-1 生产行为的修改——`views.py` 未改动，`oss_client.delete` 的实现未改动，删除接口的生产行为与 P1-1 完全一致。`apps/knowledge/tests.py` 也在规范 §39 的允许文件清单内。
- **但必须承认**：这确实是对 P1-1 时期既有测试代码的编辑，严格意义上超出了"只新增"的范围，属于 §43 #19 的边缘情形。**因此不主张无警告的 PASS。**

---

## 7. KNOWN LIMITATIONS

以下均为**本阶段有意不做**，不是遗漏：

1. **尚未接入 ClamAV**：无 `clamd` / `clamscan`，未安装、未调用、未配置（§22）。
2. **尚未接入 Rule / LLM / Vision moderation**：本轮只定义 `VERDICT_*` 值与 `findings` 结构约束，没有任何真实检测器。
3. **尚未改上传 pipeline**：上传 API **保持原行为**，不产生 `quarantine/` 对象、不写 `ContentModeration`。`build_quarantine_key` / `copy_object` / `promote_object` 目前**只有测试调用方**。
4. **尚未启用 access moderation gate**：`download` / `preview` / RAG / citation / FAQ / crawler / `data_query` 全部未加 `moderation_state == 'approved'` 门禁。提前加会导致全部历史文档被错误阻断（§31）。
5. **尚未处理历史 `KnowledgeDoc`**：migration **没有**为任何既有文档创建 `ContentModeration`（§17 默认"不要"）。**尤其没有**把 `is_sensitive=False` 映射成 `clean` —— 那会制造虚假的安全结论。历史数据策略（`legacy_unchecked` 打底 + 分批重扫）留给后续阶段。
6. **尚未处理 crawler**：爬虫入库路径的审核绕过仍在。
7. **尚未处理 FAQ**：FAQ 上传消费者路径的审核绕过仍在。
8. **尚未处理 seed bypass**：`seed_test_docs` 绕过仍在。
9. **`callback ownership`**：仍属后续阶段，本轮未推进。
10. **`findings` 脱敏未实现**：`normalize_findings` 只做长度约束。规范 §14 明确本轮**不修改** `SensitiveLog` / `sensitive_filter.py`、**不迁移**历史 `SensitiveLog`、**不把** `matched_texts` 写入 `ContentModeration` —— 已全部遵守。
11. **`build_quarantine_key` 未防同名校验**：并发下依赖 `uuid4` 后缀，不做查重。
12. **既知基线噪声（非本轮引入）**：`apps.accounts` 19 项失败；`apps.admin_panel` 存在未生成的 migration（`LoginLog` / `OperationLog`），因此 `makemigrations --check`（不加 app 参数）退出码为 1。`apps/admin_panel` 不在本轮改动范围内，`git status` 对该目录为空。

---

## 8. 后续阶段待办（§40 副产品清单，本轮**未顺手修**）

| 编号 | 内容 |
|------|------|
| P1-2B | FAQ 上传消费路径审核接入 |
| P1-2C | crawler 入库路径审核接入 |
| P1-2D | 上传流水线改造（upload → quarantine）+ access moderation gate 启用 |
| P1-2E | 历史数据 / `seed_test_docs` bypass / `find_user_docs` status bypass 处理；Celery 部署；parser 依赖；legacy data 策略 |

---

## 9. P1-2A VERDICT

**PASS WITH WARNINGS**

- 规范 §43 的 19 项成功标准中，18 项无保留满足；**唯一保留项是 #19「没有无关代码修改」**——`AdminScopeVisibilityTests`（P1-1 既有测试类）的 `setUp` 增加了一处 `oss_client.delete` mock。该改动修复的是测试隔离缺陷（原本会向生产 OSS 发出真实 DELETE 请求，直接违反 #13「没有生产 OSS 网络访问」），生产代码与 P1-1 行为未受任何影响，且 `tests.py` 在 §39 允许清单内。**两项要求在此处存在张力，本报告选择"修测试隔离"并显式披露，而非隐瞒后宣称无警告 PASS。**
- 其余关键项：`OSS_NETWORK_ACCESS: 0`（§13 达标）；`check_indexable()` 逐字节未变、`KnowledgeDoc` 未动、上传/下载/预览/RAG/FAQ/爬虫/`data_query` 未动、`settings.py` / `requirements.txt` / `deploy/*` 未动（#16 达标）；无 `state != 'rejected'` 黑名单、无 `confirmed_unchecked` 自动产生、无 migration 副作用、无 `copy+delete`、无整文件读内存（安全不变量全部成立）。
- **未 commit、未 push、未修改 `main`、未 add 任何文件。** `origin/main` 仍为 `8c2d0e3e…`。
- **本轮到此停止**，不进入 P1-2B / P1-2C / P1-2D。
