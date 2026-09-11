# Phase 1-2 Post-Fix Audit Report

> **阶段**：Phase 1-2：STEP 4-B Post-Fix / 运维安全与生产部署止血
> **日期**：2026-09-11
> **分支**：`phase-1-1-security-stopgap`
> **依据**：[P1-2D_STEP4B_Production_E2E_Audit_2026-09-11.md](P1-2D_STEP4B_Production_E2E_Audit_2026-09-11.md)（P-1 ~ P-9）
> **修改人**：3471691973-eg

---

## §1 总结

**结论：PASS WITH CONDITIONS**

六组问题（P-1/P-9、P-2、P-3、P-4/P-5、P-6、P-7）已在仓库侧全部实现并有测试固定。
三条条件项**不是实现缺陷**，而是本阶段受约束无法闭环、或需要生产侧确认的事项：

| # | 条件 | 状态 | 归属 |
|---|------|------|------|
| C-1 | 生产 WSGI 服务器依赖未决 | `BLOCKED: production WSGI dependency/plan required` | 需在 `requirements.txt` 声明 waitress 或 gunicorn（本阶段禁止新增依赖） |
| C-2 | 生产实际监听端口未确认 | `PRODUCTION_PORT_NOT_VERIFIED` | 开发机不可达生产；已在仓库内统一为 8000 并文档化 |
| C-3 | 生产 Celery 任务注册的**运行时**验证 | `NOT VERIFIED` | 本地 venv 未装 celery；已由静态断言覆盖，须在部署后重跑 |

**没有**触犯任何明令禁止项：无 STEP 4-C、无 quarantine GC、无历史数据回填、
无全量 Milvus reindex、无 collection 重建、无删除历史 OSS/数据库数据、
无 crawler / faq / Chunk TTL / Freshness / 前端改动、**0 migration**。

**特别说明（P-8）**：95 份历史文档**本阶段没有做任何处理**——没有自动回填
`confirmed_unchecked`、没有删除、没有修改、没有 reindex。按用户要求，
这属于独立的历史迁移 / 人工审核阶段。

---

## §2 P-1：`reindex_all.bat` 的破坏性操作

### 改造前（实际读到的内容）

```bat
python manage.py shell --command="...DocChunk.objects.all().delete();KnowledgeDoc.objects.all().update(status='uploaded')"
rmdir /s /q chroma_db 2>nul
python manage.py shell --command="...docs=KnowledgeDoc.objects.filter(status='uploaded')...[index_document_task(d.id) for d in docs]"
```

三条**先破坏、后重建**的操作，且完全不经过审核门：

- 中途失败（Embedding 配额耗尽 / Milvus 连不上 / Ctrl+C）→ 知识库停在
  "无分块 + 状态全 uploaded + 向量库被清空"的半毁状态，无自动恢复路径；
- `update(status='uploaded')` 抹掉"曾索引成功"的事实 —— 违反 §5.4；
- 脚本还写死了 `D:\实验室204\校园虚拟管理\backend` 这一台机器的路径。

### 改造后

| 要求 | 实现 | 验证 |
|------|------|------|
| 提供 `--dry-run`，只输出统计 | `reindex_tool.collect_stats()` + `format_stats()`；**无任何 DELETE/UPDATE/rmdir/OSS/Milvus/DocChunk 写入** | `test_dry_run_writes_nothing`（断言 `safe_delay`/`dispatch_async` 未被调用、`DocChunk` 数量不变、文档状态不变、无新增审核记录） |
| 统计口径 | 总数 / indexed / uploaded / failed / 有审核记录 / 无 / approved / confirmed_unchecked / promoted / not promoted / 可索引 / 不可索引 + **不可索引原因分布** | `test_stats_match_expected_buckets`、`test_dry_run_report_mentions_blocked_reasons` |
| 默认模式 preflight，任一文档不过门即 exit | `run_reindex()` 在 `blocked and not force` 时抛 `ReindexBlocked`；management command 转成非 0 退出码 | `test_default_mode_refuses_when_any_doc_blocked`、`test_refusal_leaves_data_untouched` |
| `--force` 不绕过审核 | `--force` 只把 `include_indexed=True`（允许重做已 `indexed` 的），**判定仍是逐条 `check_indexable()`** | `test_force_still_skips_non_indexable_documents`、`test_force_reindexes_gate_passing_document` |
| §5.4 不批量重置 `indexed` 的 status | 模块内**没有**任何 `.delete()` / `.update()` 调用 | `test_tool_has_no_delete_or_update_calls`（AST 静态断言） |
| 不写死机器路径 | `reindex_all.bat` 用 `%~dp0..\backend`，可用 `CAMPUS_BACKEND_DIR` 覆盖 | — |

**落地文件**：`apps/knowledge/reindex_tool.py`（新增）、
`apps/knowledge/management/commands/reindex_docs.py`（新增）、
`scripts/reindex_all.bat`（重写为薄封装）。

---

## §3 P-2：最小人工确认机制

**根因**：`confirmed_unchecked` 在 P1-2A 就存在于白名单与
`TERMINAL_MODERATION_STATES` 中，但**全仓库没有任何写入方**——ClamAV 不可用时
全部新上传停在 `unavailable`，运维没有任何手段放行，知识库永久停止增长。

**实现**：Django Admin Action + management command，两者共用同一条 service
`apps/knowledge/confirmation.py::confirm_unchecked_document()`。

| 强制要求 | 实现 | 验证 |
|----------|------|------|
| 仅管理员 | 复用 `apps.admin_panel.permissions.ADMIN_ROLES` | `test_anonymous_is_refused`、`test_non_admin_is_refused` |
| 显式指定 KnowledgeDoc | CLI 必填 `--doc-id`；Admin action 必须勾选记录 | — |
| 文档存在且属于本系统 | `doc.pk` + 审核记录/OSS Key 双重校验 | `test_document_without_moderation_is_refused` |
| 有效 OSS Key（隔离区） | 必须 `quarantine/` 前缀 + `head_object` 证实存在；`None`（不可验证）同样拒绝 | `test_non_quarantine_key_is_refused`、`test_missing_object_is_refused`、`test_unverifiable_object_is_refused` |
| 记录 `confirmed_by` / `confirmed_at` / `confirmation_reason` | CAS `update()` 一次写入三件套 | `test_success_writes_audit_triple` |
| 理由非空 | `reason.strip()` 为空即拒绝 | `test_empty_reason_is_refused` |
| 统一 service，禁止裸 `.update()` | 只有 `confirmation.py` 写这三个字段 | — |
| §9 不绕过 promotion | 确认后派发**既有** `moderate_and_promote_task`（`confirmed_unchecked ∈ TERMINAL_MODERATION_STATES`，检测链 skip，直接进 promotion） | `test_success_dispatches_orchestration_not_index`、`test_confirmed_state_is_terminal_for_orchestrator` |
| §10 只处理 quarantine 对象，不回填 history | `knowledge/` 前缀对象直接拒绝 | `test_non_quarantine_key_is_refused` |
| `rejected` 永不能翻案 | `CONFIRMABLE_MODERATION_STATES = {'unavailable'}` | `test_rejected_document_can_never_be_confirmed`、`test_error_state_is_not_confirmable` |
| 0 migration | 复用既有 `confirmed_by` / `confirmed_at` / `confirmation_reason` / `state` | 无新 migration 文件 |

---

## §4 P-3：RAG 读取侧审核门

**问题**：索引侧有 `check_indexable()` 门禁，但**读取侧完全没有**——任何写入
`KnowledgeDoc` 的路径（爬虫、脚本、历史数据）都能让未审核内容直接出现在问答
回答里。

**实现**：`apps/knowledge/moderation.py::filter_indexable_docs(qs)` 是唯一实现，
`utils/rag.py` 通过 `_moderation_gated()` 复用它，**不复制第二份白名单逻辑**。

接入点（与 §11 要求的四处一致）：

| 函数 | 变更 |
|------|------|
| `_apply_hard_filters` | `qs = _moderation_gated(authorized_knowledge_docs(uc).filter(status='indexed'))` |
| `_fallback_title_search` | 同上 |
| `_filter_by_permission` | 把 `allowed_ids` 限定在 `_moderation_gated(...)` 的结果内 |
| `retrieve_images` | 图片检索同样过门 |
| 新增 `authorized_knowledge_docs` 之外的 helper | `_moderation_gated()` |

fail-closed 语义（三条全部有测试）：

- 无 `ContentModeration` 记录 → 不进 RAG（`test_no_moderation_record_is_not_retrievable`）
- `storage_state != promoted` → 不进 RAG（`test_approved_but_not_promoted_is_excluded`、`test_promotion_failed_is_excluded`）
- `state ∉ INDEXABLE_MODERATION_STATES` → 不进 RAG（`test_promoted_but_rejected_is_excluded`、`test_promoted_but_unavailable_is_excluded`）
- **没有 `OR status='indexed'` 兜底**（`test_no_status_indexed_bypass`、`test_gate_reuses_single_whitelist_implementation` 静态断言 `rag.py` 不含 `MODERATION_STATE_APPROVED`）

门禁作用在真实检索通道上，不只是 helper：`test_fallback_title_search_excludes_ungated_docs`
/ `..._includes_gated_docs`。

**副作用（预期）**：95 份历史文档将从 RAG 中消失，这是 fail-closed 的设计结果。
**没有**删除它们、**没有**删除 Milvus 数据、**没有**批量改状态。

---

## §5 P-4 / P-5：异步执行能力与部署编排

### P-4 上传链与审核链解耦

**根因（比预期深一层）**：`safe_delay` 的降级只是表因。真正的元凶是
`utils/shared_task_compat._inline_runner`——未安装 Celery 时它被赋给
`task.delay`，`task.delay()` **不抛异常**却把任务体就地同步跑完，于是
`safe_delay` 的 except 分支根本不会触发。生产上 Celery 可用时它不至于同步执行，
但一旦 broker 抖动，`safe_delay` 会把整条 moderation → promotion → index
（数百秒）塞进 HTTP 请求线程，撞穿 nginx 的 `proxy_read_timeout`，并在客户端
重试时产出重复文档与重复 Embedding 计费。

**实现**：

| 文件 | 变更 |
|------|------|
| `utils/celery_dispatcher.py` | ➕ `DispatchUnavailable` + `dispatch_async()`：只入队，**无同步降级分支**。`safe_delay()` **原样保留**（§17 要求） |
| `utils/shared_task_compat.py` | 给 `_inline_runner` 打 `_inline_fallback = True` 标记，供 `dispatch_async()` 识别降级形态 |
| `apps/knowledge/upload_service.py` | `schedule_moderation()` 改走 `dispatch_async`；`DispatchUnavailable` **只记 ERROR 不重抛**，返回 `bool` |
| `apps/knowledge/tasks.py` | ➕ `_recover_undispatched_uploads()`，由既有 beat 条目 `retry-failed-moderations` 每 10 分钟调用 |

**请求路径（§20 要求）**：
`HTTP → UploadIntent → quarantine → KnowledgeDoc → commit → 入队审核 → 响应`。
入队失败时：KnowledgeDoc 保持 `uploaded`、对象留在 `quarantine/`、
**绝不**同步跑完整条链（§19）。

| 验证 | 测试 |
|------|------|
| `dispatch_async` 拒绝内联降级形态 | `test_inline_fallback_task_is_refused_not_executed` |
| broker 失败不执行任务体 | `test_broker_failure_is_refused_not_executed` |
| `safe_delay` 降级能力未被破坏（§17） | `test_safe_delay_still_degrades` |
| 队列不可用 → 返回 False、文档/对象不变 | `test_queue_down_leaves_document_untouched` |
| 队列不可用 → 上传请求不 500 | `test_queue_down_does_not_raise` |
| 补扫只挑 quarantine + uploaded + 无审核 + 超宽限期 | `test_picks_quarantine_doc_without_moderation` |
| 补扫不碰历史/正式区文档（§10/§14） | `test_official_key_document_is_skipped`、`test_indexed_document_is_skipped` |
| 补扫绝不放行 | `test_recovery_never_approves` |

所有入口测试同时断言 **`safe_delay` 未被调用**——若有人把入口改回降级派发，
测试立刻失败（`tests.py` 三处夹具）。

### P-5 部署编排

| 要求 | 实现 |
|------|------|
| 不使用 `runserver` 作生产 WSGI（§22） | `start.bat` 显式降级为"仅限本地开发"；新增 `deploy/start_web.bat` **拒绝**回退 runserver |
| 不擅自新增依赖（§23） | 未改 `requirements.txt`；`start_web.bat` 探测 waitress/gunicorn，缺失则退出码 2 打印 `BLOCKED: production WSGI dependency/plan required` |
| 统一端口（§24） | 仓库内统一为 8000（原 `nginx.conf` 8000 vs `start.bat` 3000）；生产实际端口 **PRODUCTION_PORT_NOT_VERIFIED**，可经 `CAMPUS_PORT` 覆盖 |
| worker 必须加载 `config.celery` 并发现三个任务（§25） | 新增 `deploy/start_worker.bat`（`-A config`，Windows 默认 `--pool=solo`）；静态断言 + skipUnless 的运行时断言 |
| beat 必须跑 `retry-failed-moderations` 且不删既有条目（§26） | 既有 `config/celery.py` 已含该条目（`*/10`）；新增 `deploy/start_beat.bat`；`test_celery_config_declares_all_beat_entries` 逐条固定 6 个 key |
| 不破坏现有 beat 调度 | 6 条全部保留并有断言 |

> 本阶段**没有**新增 `waitress` / `gunicorn` 到 `requirements.txt`——§23 明确
> 要求先报告而不是自行扩大范围。这是条件项 C-1。

---

## §6 P-6：生产安全配置

**问题**：`ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*')`。生产 `.env`
漏配或写空时，Django **静默接受任意 Host 头**，没有任何报错。

| 要求 | 实现 |
|------|------|
| 不硬编码生产域名（§27） | `nginx.conf` 的 `server_name` 改为占位符 `_`；域名只经环境变量 |
| 用 `DJANGO_DEBUG` / `DJANGO_ALLOWED_HOSTS` / `CORS_ALLOWED_ORIGINS` | 三个新名优先；同时兼容旧名 `DEBUG` / `ALLOWED_HOSTS` / `CORS_ORIGINS`（已有 `.env` 不失效） |
| 生产 `DEBUG=False` | 默认即为 `False`，且 `SECRET_KEY` 为默认开发密钥时**拒绝启动**（既有守卫） |
| 生产 `ALLOWED_HOSTS` 不含 `*` | 缺省不再是 `*`；`DEBUG=False` 时未配置或含 `*` → `ImproperlyConfigured` |
| 生产 `CORS_ALLOW_ALL_ORIGINS=False` | 显式设 True 时**拒绝启动**；生产 `CORS_ALLOWED_ORIGINS` 不再兜底 localhost（改为空列表，fail-closed） |
| 本地开发仍可用（§31） | `DEBUG=True` 时 `*` 与 `localhost:5173` 兜底照旧 |

验证（直接加载 `config/settings.py` 源码，注入环境变量）：
`test_production_with_wildcard_hosts_refuses_to_boot`、
`test_production_without_hosts_refuses_to_boot`、
`test_production_with_wildcard_cors_refuses_to_boot`、
`test_production_with_explicit_values_boots`、
`test_production_never_defaults_cors_to_localhost`、
`test_development_still_works`、`test_legacy_variable_names_still_honoured`。

**未修改** `apps.accounts` / SSO / 登录 / JWT / 权限；19 条既有失败原样保留
（见 §9）。

---

## §7 P-7：向量后端故障可观测性

**问题**：`search()` 在"后端不可用"与"确实没有匹配"两种情况**都返回 `[]`**，
于是"向量库挂了"在问答侧表现为"知识库里没有相关内容"——故障被伪装成正常空结果。

**实现**：

| 变更 | 位置 |
|------|------|
| 返回类型改 `Optional[List[dict]]`：`None` = 后端不可用，`[]` = 检索成功无匹配 | `utils/milvus.py::search()` |
| `backend_name()` → `'milvus'` / `'chroma'` / `'unavailable'` | `utils/milvus.py` |
| `_search_chroma` 的 ImportError / 集合缺失 → `return None`（原 `[]`） | |
| `_brute_force_chroma` 取数失败 → `return None`（原 `[]`） | |
| `retrieve()` 向量循环显式分支，记 ERROR 日志 | `utils/rag.py` |
| ➕ `vector_backend_available()` 供健康检查 / 未来用户侧提示使用 | `utils/rag.py` |

**禁止项**：不存在静默 `不可用 → []` 的路径。验证：
`test_search_returns_none_without_backend`、`test_backend_name_reports_unavailable`、
`test_empty_result_is_not_unavailable`、`test_vector_backend_available_helper`。

> 用户侧提示（`apps/chat/views.py`）不在本阶段允许的文件范围内，故本轮只
> 提供 helper + 日志，未改前端可见行为。

---

## §8 P-8：历史 95 份文档

**本阶段没有对历史数据做任何操作。**

| 动作 | 是否发生 |
|------|----------|
| 自动回填 `confirmed_unchecked` | **没有** |
| 创建 `ContentModeration` 记录 | **没有** |
| 删除历史数据库数据 | **没有** |
| 删除历史 OSS 对象 | **没有** |
| 全量 reindex / Milvus collection 重建 | **没有** |
| 批量修改 `KnowledgeDoc.status` | **没有** |

预期后果（§14，设计如此）：这 95 份文档因 fail-closed 从 RAG 结果中消失。
它们是"不可索引"状态，`reindex_docs` 默认模式会因此**直接退出不做任何写入**
（`ReindexBlocked`），这是预期行为而非故障。

处理方式属于独立的历史迁移 / 人工审核阶段（P-8），本阶段**立即停止**，
不继续推进（§三十九）。

---

## §9 测试

### 9.1 基线（改造前实测）

| 范围 | 结果 |
|------|------|
| `apps.knowledge` | **439 OK** |
| 全量 | 471 tests，**15 failures + 4 errors，全部位于 `apps.accounts`** |

### 9.2 本次变更后

| 范围 | 结果 |
|------|------|
| `apps.knowledge` | **513 OK**（skipped=1） |
| 全量 | 545 tests，**15 failures + 4 errors（skipped=1）** |
| `apps.knowledge.test_phase1_2_postfix`（新增） | **74 OK**（skipped=1） |

### 9.3 failure set 逐条比对

新失败集合与基线**完全一致**，无一条新增、无一条位于 `apps.knowledge`：

```
ERROR  apps.accounts.tests.SsoMockTests.test_callback_jwt_can_access_protected_endpoint
ERROR  apps.accounts.tests.SsoMockTests.test_callback_returns_existing_user
ERROR  apps.accounts.tests.SsoMockTests.test_callback_updates_existing_user_info
ERROR  apps.accounts.tests.EdgeCaseTests.test_sso_user_can_access_me
FAIL   apps.accounts.tests.SsoMockTests.test_callback_all_three_codes_work
FAIL   apps.accounts.tests.SsoMockTests.test_callback_creates_new_user
FAIL   apps.accounts.tests.SsoMockTests.test_callback_empty_string_code
FAIL   apps.accounts.tests.SsoMockTests.test_callback_invalid_code
FAIL   apps.accounts.tests.SsoMockTests.test_callback_missing_code
FAIL   apps.accounts.tests.SsoMockTests.test_callback_sso_uid_unique
FAIL   apps.accounts.tests.LoginApiTests.test_change_password_success
FAIL   apps.accounts.tests.LoginApiTests.test_change_password_weak_new
FAIL   apps.accounts.tests.LoginApiTests.test_change_password_wrong_old
FAIL   apps.accounts.tests.LoginApiTests.test_me_get
FAIL   apps.accounts.tests.LoginApiTests.test_me_put
FAIL   apps.accounts.tests.LoginApiTests.test_me_put_cannot_change_role
FAIL   apps.accounts.tests.SsoMockTests.test_sso_login_returns_mock_codes
FAIL   apps.accounts.tests.SsoMockTests.test_sso_login_returns_usage_hint
FAIL   apps.accounts.tests.EdgeCaseTests.test_sso_user_cannot_jwt_login
```

这 19 条是阶段外既有缺陷，按 §31 要求**未被修复、未被跳过、未被改写**。

测试规模变化：471 → 545（+74，即新增的 `test_phase1_2_postfix.py`）。

### 9.4 关于 1 条 skip

`ProductionSettingsGuardTests.test_celery_registers_moderation_tasks` 使用
`unittest.skipUnless(_celery_installed(), ...)`。原因是**本机 venv 未安装
celery**（它是 `requirements.txt` 中的可选依赖），导入 `config.celery` 必然
`ModuleNotFoundError`。

**这不是为了让测试变绿而跳过安全检查**：同一组功能已由 4 条**始终执行**的静态
断言覆盖（beat 6 个条目齐全、`autodiscover_tasks()` 存在、
`retry-failed-moderations` 指向正确任务、三个关键任务都有 `@shared_task` 装饰器）。
仅"运行期任务是否真的注册进 app"一项无法在本地验证 → 记为条件项 **C-3
（NOT VERIFIED）**，部署后须重跑。

**没有** 使用 skip / xfail 掩盖任何一项新增安全逻辑；**没有** 修改 baseline
assertion；**没有** 删除旧测试。

---

## §10 Git

```
分支：phase-1-1-security-stopgap   （未切换、未新建）
git add  ：NO     （git diff --cached 为空）
git commit：NO
git push ：NO
git reset / checkout / stash / clean / 删除文件：NO
git diff --check：clean（无空白错误）
```

工作区改动 = 本阶段新增/修改 + 上一阶段（STEP 3 / 4-A / 4-B）遗留的未提交内容，
全部原样保留，未做任何回退。

---

## §11 未解决问题

| 编号 | 问题 | 状态 | 说明 |
|------|------|------|------|
| C-1 | 生产 WSGI 服务器（waitress / gunicorn）未在 `requirements.txt` 声明 | **BLOCKED: production WSGI dependency/plan required** | 按 §23 未擅自新增依赖。补齐后 `deploy/start_web.bat` 无需修改即生效 |
| C-2 | 生产实际监听端口 | **PRODUCTION_PORT_NOT_VERIFIED** | 开发机不可达生产。仓库内已统一为 8000，上线前须确认 |
| C-3 | 生产 Celery 任务注册的运行时验证 | **NOT VERIFIED** | 本地 venv 无 celery；静态断言已覆盖，部署后重跑该条测试 |
| C-4 | 生产环境是否已按 §6 设置 `DJANGO_ALLOWED_HOSTS` / `CORS_*` | **NOT VERIFIED** | `.env` 不在仓库内且生产不可达。守卫已就位：配错会拒绝启动 |
| C-5 | 生产 RDS / ClamAV / Milvus / Celery broker 可达性 | **NOT VERIFIED** | 沿用上阶段审计结论（本机仅 OSS 可达） |
| C-6 | 95 份历史文档的处置 | **REQUIRES NEXT PHASE** | 属 P-8 独立阶段；本阶段按 §三十九**立即停止** |
| C-7 | `quarantine/` 对象 GC | **REQUIRES NEXT PHASE** | 属 STEP 4-C，明令不在本阶段范围 |
| C-8 | 向量后端不可用的**用户侧**提示 | **REQUIRES NEXT PHASE** | 本阶段已提供 `rag.vector_backend_available()` + ERROR 日志；`apps/chat/views.py` 不在允许范围内 |
| C-9 | 本地 venv 缺少 PyMuPDF / pdfplumber / python-pptx / openpyxl / xlrd / celery | 环境差异（非仓库缺陷） | `requirements.txt` 已声明这些依赖；属本机 venv 未安装，**不是生产发现** |

---

## 附：本阶段文件清单

**新增**：`apps/knowledge/reindex_tool.py`、`apps/knowledge/confirmation.py`、
`apps/knowledge/admin.py`（原为 2 行占位，重写）、
`apps/knowledge/templates/admin/knowledge/confirm_selected.html`、
`apps/knowledge/management/commands/{reindex_docs,confirm_moderation}.py`、
`apps/knowledge/test_phase1_2_postfix.py`、
`deploy/{start_web,start_worker,start_beat}.bat`、
`docs/生产部署编排说明-2026-09-11.md`、本报告。

**修改**：`utils/celery_dispatcher.py`、`utils/shared_task_compat.py`、
`utils/milvus.py`、`utils/rag.py`、`apps/knowledge/moderation.py`、
`apps/knowledge/upload_service.py`、`apps/knowledge/tasks.py`、
`apps/knowledge/tests.py`、`config/settings.py`、`deploy/nginx.conf`、
`start.bat`、`scripts/{reindex_all.bat,index_all_docs.py,index_quick.py}`、
`code-changes/2026-09-10.md`。

**未修改**（明令禁止）：`models.py`、任何 migration、`UploadIntent` schema、
`ContentModeration` schema、promotion primitive、`utils/crawler.py`、
`apps/faq/`、Milvus schema、前端、accounts/SSO/JWT/权限。
