# Phase 1-1 高风险安全止血 — 实施报告

> **基线 Commit**：`c402ed750deba5ae12a0c86f8d48f1ece7c49a13`（== `origin/main`，已核对）
> **报告时间**：2026-09-10
> **报告位置**：仓库根目录（**未纳入 git**，请勿提交）
> **工作目录**：`e:\github\campus_resource\backend`

---

## 0. 结论速览

| 项 | 值 |
|----|----|
| `EMPTY_FILE_POLICY` | **`ALLOWED`**（保持现有产品行为，未恢复 `c402ed75` 撤销的空文件拒绝逻辑） |
| `Database Changes` | **`NONE`**（未修改任何 `models.py`，未产生 migration） |
| 新增/修改文件 | 7 改 + 1 新增（生产代码），1 改（测试） |
| 测试 | `apps.knowledge` 57 项全通过；全量 89 项中 19 项失败，**均为基线已存在**（见 §6） |
| `callback ownership` | **`BLOCKED`**（不得声称已完全解决，见 §8.1） |
| 内容安全审核门 | **`DEFERRED to P1-2`**（仅建立扩展点，见 §8.2） |
| **`Result`** | **`PARTIAL`** — 实施范围内 12 项全部完成并通过测试；PARTIAL 仅因上两项按规范要求**不得**在本阶段完成 |

---

## 1. 实施范围（已全部完成）

| # | 项 | 文件 | 状态 |
|---|----|------|------|
| 1 | OSS 服务端权威元数据读取 `head_object` | [utils/oss.py](backend/utils/oss.py) | ✅ |
| 2 | 内部状态字段设为只读（`status` / `is_sensitive` / `sensitive_checked`） | [apps/knowledge/serializers.py](backend/apps/knowledge/serializers.py) | ✅ |
| 3 | 大小上限常量 + 可复用 `check_file_size` | [apps/knowledge/serializers.py](backend/apps/knowledge/serializers.py) | ✅ |
| 4 | 回调对象真实性校验（存在性 / 大小 / 类型 / ETag-MD5） | [apps/knowledge/views.py](backend/apps/knowledge/views.py) | ✅ |
| 5 | 回调 `oss_key` 归属范围校验 | [apps/knowledge/views.py](backend/apps/knowledge/views.py) | ✅ |
| 6 | 同 `oss_key` 重复登记拦截 | [apps/knowledge/views.py](backend/apps/knowledge/views.py) | ✅ |
| 7 | R26 越权可见性修复（列表 + 删除 + 归属改写） | [apps/knowledge/views.py](backend/apps/knowledge/views.py) | ✅ |
| 8 | 分片上传大小 / 序号 / 跨用户校验 | [apps/knowledge/chunked_views.py](backend/apps/knowledge/chunked_views.py) | ✅ |
| 9 | 合并事务化 + 行级锁（防重复/并发合并） | [apps/knowledge/chunked_views.py](backend/apps/knowledge/chunked_views.py) | ✅ |
| 10 | 索引任务集中式前置校验扩展点 | [apps/knowledge/moderation.py](backend/apps/knowledge/moderation.py)（新增） | ✅ |
| 11 | 孤立索引任务统一转发 | [config/celery.py](backend/config/celery.py) | ✅（见 §7.2 未验证说明） |
| 12 | 回归测试 46 项 | [apps/knowledge/tests.py](backend/apps/knowledge/tests.py) | ✅ |

**范围未扩大**：未修改 `models.py`、未新增 migration、未改 `settings*.py`、未改 Bucket ACL、未连接生产 MySQL/Redis/Milvus、未改前端代码。

---

## 2. 关键实现说明

### 2.1 为什么"死字段"没有被包装成安全机制（对应 §10.4）

`is_sensitive` / `sensitive_checked` 在代码库中**没有任何写入方**，是事实上的死字段。本次：

- 将它们加入 `read_only_fields`——目的是**阻止客户端伪造**，而不是把它们当审核结论；
- `apps/knowledge/moderation.py` 的校验体中**没有**任何 `if not doc.is_sensitive and doc.sensitive_checked` 形式的逻辑，只有一段注明 P1-2 接入点的注释；
- 测试 `test_central_check_does_not_use_dead_moderation_fields` 刻意把两个字段设成"看起来已审核通过"的值，断言校验函数**仍然放行**——即证明它们不构成审核门。

### 2.2 为什么校验放在任务体内而非视图层（对应 §10）

`index_document_task` 存在 **6 条执行路径**：视图 `safe_delay`、`safe_delay` 同步降级、脚本 `task.run()`、爬虫任务、`config/celery.py` 残留任务名、broker 残留消息。只在视图层加校验会被后 5 条绕过，因此校验落在任务体第一段。

同时**没有**让所有历史文档无法索引（对应 §10.5）：当前校验只要求 `oss_key` 非空且以 `knowledge/` 开头，`knowledge/college/0/...` 这类历史 key 同样放行。真正的审核门以**扩展点**形式预留。

### 2.3 为什么 `0` 不再等于"不检查"（对应 §8.2）

分片初始化区分"未提供"与"确实为 0"（两者都落库为 0，但 `0` 是明确值）。合并时：

- `total_size > MAX_FILE_SIZE` 与 `check_file_size(total_size, ext)` **无条件生效**，与 `session.file_size` 是否为 0 无关；
- `session.file_size` 仅在非 0 时用于一致性比对。

即 `file_size=0` 只关闭"声明值 vs 实测值比对"，不再关闭任何大小校验。

### 2.4 `safe_delay` 移出事务

合并视图在事务提交后才触发 `extract_freshness_task` / `index_document_task`。原因：Celery/Redis 不可用时 `safe_delay` **同步执行整条索引链路**（下载 OSS → 解析 → Embedding → 写 Milvus），留在事务内会长时间持锁。

---

## 3. 变更规模

```
 backend/apps/knowledge/chunked_views.py | 148 +  97 -
 backend/apps/knowledge/serializers.py   |  28 +   5 -
 backend/apps/knowledge/tasks.py         |  13 +   0 -
 backend/apps/knowledge/tests.py         | 726 +   2 -
 backend/apps/knowledge/views.py         | 155 +  23 -
 backend/config/celery.py                |  27 + 291 -
 backend/utils/oss.py                    |  51 +   0 -
 + 新增 backend/apps/knowledge/moderation.py
```

`config/celery.py` 的 291 行删除见 §5；`chunked_views.py` 的 97 行删除主要是合并视图整体缩进（事务包裹）产生的行级重排，**无行为删除**。

---

## 4. `EMPTY_FILE_POLICY = ALLOWED` 的落实证据

| 位置 | 行为 |
|------|------|
| `ChunkUploadInitView` | `file_size` 为 0 或缺失 → 接受，落库 0（**不拒绝**） |
| `ChunkMergeView` | 实测总大小为 0 → 仍然执行上限校验，但不因"为 0"拒绝 |
| 测试 | `test_init_allows_zero_declared_size` / `test_init_allows_missing_declared_size` 断言 200 |

已交叉核对：本次 diff 的**新增行**中不存在任何 `file_size == 0` 形式的拒绝分支，也未改动 `_validate_file_size` 的原有阈值语义。**未恢复** `c402ed7` 撤销的任何逻辑。

---

## 5. `code-changes/2026-09-10.md` 说明

已按 [CONVENTIONS.md](docs/CONVENTIONS.md) §12 模板在仓库根目录**新建** [code-changes/2026-09-10.md](code-changes/2026-09-10.md)。

⚠️ 注意：同名文件曾由 commit `c402ed7` 删除（内容为"禁止空文件上传"，与本次无关）。新文件开头已显式注明两者无关、**未恢复**任何空文件拒绝逻辑，避免后续误读历史。

---

## 6. 测试与验证

### 6.1 执行结果

```
manage.py check --settings=config.settings_test        → System check identified no issues
manage.py test apps.knowledge --settings=config.settings_test
                                                       → Ran 57 tests ... OK
manage.py test --settings=config.settings_test          → Ran 89 tests ... FAILED (failures=15, errors=4)
```

### 6.2 全部 19 项失败均为基线既有，与本次变更无关

在基线 `c402ed75` 的独立 git worktree 中复跑同一套件：

```
基线：Ran 43 tests ... FAILED (failures=15, errors=4)   ← 与本次完全相同的 19 项
本次：Ran 89 tests ... FAILED (failures=15, errors=4)   ← 新增 46 项全部通过
```

19 项失败**全部位于 `apps.accounts`**（SSO mock 模式未启用返回 501、JWT `/me` 等），本次未改动 `apps.accounts` 及其依赖。worktree 已删除（`git worktree list` 仅剩主工作区）。

### 6.3 未能验证的部分（如实上报）

| 项 | 原因 |
|----|------|
| `config/celery.py` 的转发改动 | 本机 venv 未安装 celery（`ModuleNotFoundError: No module named 'celery'`），该模块无法导入，测试覆盖不到。仅通过 `py_compile` 语法校验。**上线前需在装有 celery 的环境复验一次。** |
| `select_for_update()` 的真实并发串行化 | **SQLite 上它是静默空操作**（Django compiler 的 `has_select_for_update` 为 False）。因此 `test_merge_rejects_duplicate_merge` 验证的是**同连接下的重复合并拒绝**，真实的跨连接并发行为未在测试中触发，需在 MySQL/GaussDB 环境验证。 |
| Content-Type 校验的宽松分支 | OSS 对象为通用类型（空 / `application/octet-stream`）时跳过比对——前端 PUT 未显式指定 Content-Type 时会写入通用值，硬比对会误伤正常上传。该"逃生口"已用 `test_accepts_generic_content_type` 固定为**预期行为**而非疏漏。 |

---

## 7. 安全合规复核

### 7.1 §21「测试不得访问生产环境」— **发现 1 次违规，已修复**

**首次测试运行中，2 个分片合并用例真实访问了生产 OSS Bucket。**

- **成因**：测试 patch 目标是 `utils.celery_dispatcher.safe_delay`，但 `chunked_views.py` 在模块导入时已用 `from ... import safe_delay` 绑定，patch 不生效。Celery 缺失导致 `safe_delay` 降级为同步执行，`extract_freshness_task` 与 `index_document_task` 因此真的运行并请求 OSS。
- **实际影响**：向 `campus-resourse.oss-cn-chengdu.aliyuncs.com` 发出 **4 次签名 GET 请求**（2 个用例 × 2 个任务），目标 key 均为测试构造的 `knowledge/college/1/2026-09/chunk.pdf`（生产不存在）。**4 次全部返回 404**，未读取、未写入、未删除任何生产对象。
- **修复**：patch 目标改为 `apps.knowledge.chunked_views.safe_delay`，并在测试方法中写明原因。修复后复跑，日志中已无任何 `aliyuncs` 请求。
- **未连接**：MySQL / GaussDB / Redis / Milvus / Celery broker 全程未连接（`settings_test` 使用内存 SQLite，Celery 未安装）。

### 7.2 敏感信息

- 本次所有输出未打印 AccessKey / SecretKey / Token / 密码。
- 为在基线 worktree 中复跑测试，曾用 `set -a; . .env` 将 `.env` 导入**当前进程环境**（未打印、未复制到磁盘），worktree 已删除。
- **沿用上一轮遗留披露**：Phase 1-0 期间的 `.env` 脱敏正则 `(SECRET|KEY|PASSWORD|TOKEN)=` 未匹配 `OSS_ACCESS_KEY_ID=`，AccessKey **ID**（非 Secret）曾输出到终端。若终端日志可能留存或分享，建议轮换 AK。

### 7.3 §23 git 检查

| 检查 | 结果 |
|------|------|
| `git status --short` | 仅 7 个预期文件 + 新增 `moderation.py`；**无**临时文件、无构建产物 |
| `git diff --stat` | 见 §3，全部落在预期范围内 |
| `git diff --check` | 无空白错误（仅有 `celery.py` 的 LF→CRLF 提示，属仓库既有配置） |
| 调试代码 | 新增行中**无** `print(` / `pdb` / `breakpoint(` / 新增 `TODO` |
| 意外改动 | **无** `models.py` / `migrations/` / `settings*.py` 改动 |
| 测试代码混入生产逻辑 | **无**，测试全部位于 `apps/knowledge/tests.py` |
| 是否误恢复 `c402ed7` 撤销的行为 | **否**，见 §4 |

**未跟踪文件**：`security_moderation_phase0_audit.md`、`security_moderation_phase1_0_environment.md`、本报告，以及 `code-changes/2026-09-10.md`。前三份报告**不应提交**；变更记录**应当提交**。

---

## 8. 未完成 / 需后续处理项

### 8.1 `callback ownership` = **`BLOCKED`**

**不得声称 callback ownership 已完全解决。**

本次已把"客户端声明不可信"这一层做到了服务端权威校验（对象存在性 / 大小 / 类型 / 内容 MD5 / key 归属范围），但 `_verify_oss_object` 只能证明：

> "该 OSS 对象存在，且其服务端元数据与本次声明一致。"

**不能**证明：

> "该对象是由本次请求的发起者上传的。"

STS 直传路径下，服务端从未持有一份"我签发过这个 key 给这个用户"的可信凭证，因此**同学院的两名管理员可以互相认领对方刚上传的对象**（前者需要知道完整 oss_key，且文件 MD5 与大小需能对上——攻击面已大幅收窄，但缺口客观存在）。补全需要一张服务端上传会话（pending upload）记录表。

按 §6.5 约束，**未**临时发明半成品 `PendingUpload` 模型，而是将缺口如实上报。建议在 P1-2 随内容审核状态模型一并落地。

### 8.2 内容安全审核门 = **`DEFERRED to P1-2`**

`apps/knowledge/moderation.py` 中已留出唯一接入点。当前未实现：ClamAV / LLM / Vision / OCR 审核、quarantine 存储、OSS quarantine → 正式态流转、完整 upload pipeline、完整异步审核轮询。

### 8.3 其他已识别但本次未处理（未扩大范围，仅记录）

| 项 | 说明 |
|----|------|
| `oss_key` / `md5_hash` 对合法授权用户可见 | `KnowledgeDocSerializer` 会把二者返回给有权查看该文档的用户。是否需要在接口层收敛属产品决策，本次未改（改动会破坏下载/预览链路）。 |
| `PUT /api/knowledge/docs/{id}/` 与文档串不符 | 视图 docstring 称 PUT "按需传入字段"，但 `RetrieveUpdateAPIView` 的 PUT 是全量更新。本次未改（属既有行为，前端实际用 PATCH），仅测试改用 PATCH。 |
| nginx 500m 与绝对上限 500MB | 按用户决策对齐，未调整 nginx。 |

---

## 9. 待人工确认

1. `config/celery.py` 请在装有 celery 的环境复验转发行为后再上线。
2. 合并并发保护请在 MySQL/GaussDB 环境验证 `select_for_update()` 生效。
3. 是否同意按 §8.1 / §8.2 将两项遗留问题纳入 P1-2 范围。
4. 本次变更**尚未提交**（工作区为已修改状态），是否提交 / 提交信息由你决定。
