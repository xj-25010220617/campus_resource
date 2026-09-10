# Phase 1-2 Design Audit — 内容安全审核体系（只读设计 / 影响面审计）

========================================
Phase 1-2 Design Audit
========================================

BASELINE:
174a64889643a09ebbb4e23d605ae4b8f131ac24

ORIGIN_MAIN:
8c2d0e3e7fb4fad87e4ffb2b45055c880c8e91df

WORKTREE_MODIFIED:
NO
（`git status --short` 仅有 3 个 untracked 的既有审计报告；无 tracked 文件被修改）

DATABASE_CHANGED:
NO
（未执行 migrate / makemigrations；未连接任何数据库；本报告仅静态阅读 models.py 与 migrations/）

OSS_CHANGED:
NO
（未上传 / 下载 / 删除 / 复制任何 OSS 对象；未发起任何 OSS 请求）

PRODUCTION_CONNECTION:
NO
（未连接生产 MySQL/GaussDB、Redis、Milvus、Celery broker、LLM、Vision、ClamAV；未启动任何服务）

P1-1_MODIFIED:
NO
（未修改 P1-1 的任何文件；§二 列出的 8 个对象逐一保持原样）

----------------------------------------

## 0. 本轮审计方法与可信度声明

- 所有结论均来自**当前工作区代码**（`174a648` = `8c2d0e3` + P1-1 commit），未引用旧报告行号。
- 标注为 `[已核实]` 的事实，均由 `read` / `grep` 直接读到的代码或配置支撑，附 `file:line`。
- 标注为 `[建议]` 的内容是设计提案，**本轮不实现**。
- 未运行任何测试、管理命令、脚本或网络请求。
- 三处 `[存疑/待人工确认]` 已在正文标出，未做猜测性断言。

---

## 1. UPLOAD_ENTRYPOINT_AUDIT（P1-2-A）

### 1.1 全量入口清单（`[已核实]`，基于当前代码）

| # | 入口 | 路由 / 触发 | 权限类 | OSS 写入方式 | oss_key 形态 |
|---|------|------------|--------|-------------|-------------|
| E1 | `FileUploadView` | `POST /api/knowledge/upload/file/` | `IsAuthenticated + IsAdminOrSuperAdmin` | 后端 `put_object`（**整个文件读进内存**） | `knowledge/{scope}/{owner}/{YYYY-MM}/{uuid}.{ext}` |
| E2 | `BatchFileUploadView` | `POST /api/knowledge/upload/batch/` | 同上 | 同上，逐文件 | 同上 |
| E3 | `TextUploadView` | `POST /api/knowledge/upload/text/` | 同上 | `oss_client.upload_text`（`put_object`） | `knowledge/{scope}/{owner}/{YYYY-MM}/{uuid}.txt`（**写死 .txt**） |
| E4 | `UploadSTSView` | `POST /api/knowledge/upload/sts/` | 同上 | **签发前端直传凭证**（`sign_url('PUT')`，生产 STS 代码被注释掉） | 由 `get_sts_credentials` 生成，同上形态 |
| E5 | `UploadCallbackView` | `POST /api/knowledge/upload/callback/` | 同上 | 不写 OSS；**登记前端已传对象** | 客户端提交，P1-1 已加 key 格式 + scope + `head_object` 三态校验 |
| E6 | `ChunkUploadInitView` | `POST /api/knowledge/upload/chunk/init/` | 同上 | `init_multipart_upload` | `knowledge/{scope}/{owner}/{YYYY-MM}/{12hex}.{ext}`（`chunked_views.py:50-61`） |
| E7 | `ChunkUploadView` | `POST /api/knowledge/upload/chunk/` | 同上 | `upload_part`（流式转发，不落本地盘） | 同 E6 |
| E8 | `ChunkMergeView` | `POST /api/knowledge/upload/chunk/merge/` | 同上 | `complete_multipart_upload` | 同 E6 |
| E9 | `utils/crawler.save_page_as_knowledge_doc` | 由 `crawl_and_index` 调用 | 无（内部） | `oss_client.upload_text` | `knowledge/website/0/{YYYY-MM}/crawl_{12hex}.txt`（`crawler.py:448-449`） |
| E10 | `scripts/index_all_docs.py` / `index_quick.py` | 手动脚本 | 无 | 不写 OSS | — |
| E11 | `scripts/seed_test_docs.py` | 手动脚本 | 无 | **自有写入逻辑** | `seed/{title}.md` |
| E12 | `scripts/rebuild_milvus.py` | 手动脚本 | 无 | 不写 OSS | —（**drop 整个 collection**） |

### 1.2 六条主路径的现状矩阵（`[已核实]`）

| 路径 | 写 OSS | 建 KnowledgeDoc | 有 moderation | 有 quarantine | 占 `knowledge/` | 触发 index | 进 RAG |
|------|--------|----------------|--------------|--------------|---------------|-----------|--------|
| E1 文件直传 | 是（`views.py:1332`） | 是（`views.py:1450`） | ❌ 无 | ❌ 无 | ✅ 直接进 | ✅ `views.py:1501` | ✅ 索引后即进 |
| E2 批量 | 是（`views.py:1629`） | 是（`views.py:1672`） | ❌ 无 | ❌ 无 | ✅ 直接进 | ✅ `views.py:1721` | ✅ |
| E3 文本录入 | 是（`views.py:1002`） | 是（`views.py:1024`） | ❌ 无 | ❌ 无 | ✅ 直接进 | ✅ `views.py:1057` | ✅ |
| E4 STS 直传 | 否（前端直传） | 否 | ❌ 无 | ❌ 无 | ✅ 直接进 | —（等 E5） | ✅ |
| E5 回调登记 | 否 | 是（`views.py:748`） | ❌ 无 | ❌ 无 | 对象已在 | ✅ `views.py:786` | ✅ |
| E6-E8 分片 | `init`+`part`+`complete` | 是（`chunked_views.py:363`） | ❌ 无 | ❌ 无 | ✅ 直接进 | ✅ `chunked_views.py:398` | ✅ |
| E9 爬虫 | 是（`crawler.py:451`） | 是（`crawler.py:463`） | ❌ 无 | ❌ 无 | ✅ `knowledge/website/` | ✅ `crawler.py:491` | ✅ |

**结论**：当前**没有任何一条路径**存在 moderation 或 quarantine。`check_indexable()`（`moderation.py:37`）只校验 `oss_key` 非空且以 `knowledge/` 开头 —— 而**全部 12 个入口生成的 key 都满足这个前缀**，所以这个结构性闸门目前对上述所有路径都是放行的。

### 1.3 现有唯一"审核相关"闸门的真实能力（`[已核实]`）

`apps/knowledge/moderation.py::check_indexable(doc) -> (bool, str)`：

- 检查 1：`doc.oss_key` 非空；
- 检查 2：`doc.oss_key.startswith('knowledge/')`；
- 明确的 P1-2 接入点已留注释（`moderation.py:57-66`），并明确禁止把 `is_sensitive` / `sensitive_checked` 当审核结论。

`is_sensitive` / `sensitive_checked` 的**全部写入点**只有 `apps/knowledge/tests.py:511,918` —— 生产代码零写入，确认为死字段（`[已核实]`）。

---

## 2. 现状中必须优先处理的结构性缺口（先于任何实现）

这些不是设计建议，是审计发现的**当前代码事实**，直接影响 P1-2 的安全性：

### 2.1 `scripts/seed_test_docs.py::index_seed_docs` 完全绕过闸门（`[已核实]`）

`scripts/seed_test_docs.py:857-959` 自行实现 chunking → embedding → `milvus_client.insert_chunks`（`:916`）→ `DocChunk.objects.bulk_create`（`:932`），**不调用 `index_document_task`，因此不经过 `check_indexable`**。其 `oss_key` 为 `seed/{title}.md`，本来也不满足 `knowledge/` 前缀。

→ P1-2 的闸门若只建在任务体内，**这条路径依然可写入 DocChunk + Milvus**。必须让写入收敛到单一函数（或让该脚本改为调用任务）。

附带缺陷：`:826` 调用 `milvus_client.delete_by_doc_id(did)`，而 `utils/milvus.py` 只定义了 `delete_by_doc` —— 该方法不存在，`AttributeError` 被 `:823-828` 的 `try/except` 静默吞掉，**旧向量实际未被删除**。

### 2.2 `safe_delay` 的"静默不执行"路径（`[已核实]`）

`utils/celery_dispatcher.py:32-49`：`task.delay()` 抛异常 → 降级 `task(*args)`；若降级调用**也**抛异常 → 记 error log 后 `return None`。**异常不外传，调用方全部忽略返回值**（`views.py:786,1057,1501,1721`、`chunked_views.py:398`、`crawler.py:491` 均未检查返回值）。

→ 对 P1-2 的含义：moderation 的"未通过"必须以**写状态**（而非抛异常）表达。若未来的闸门改为 `raise`，在 broker 故障 + 闸门抛错同时发生时，文档会静默停留在 `uploaded`，既不索引也不报错。

### 2.3 `select_for_update()` 在 SQLite 上是空操作（`[已核实]`，P1-1 已知）

`ChunkMergeView` 的并发保护（`chunked_views.py:282`）只在 MySQL/GaussDB 上有效；测试环境（`config/settings_test.py` → 内存 SQLite）无法验证真实串行化。

### 2.4 无 celery worker 的部署单位（`[已核实]`）

`.github/workflows/deploy.yml` 只 `systemctl restart campus-backend`；全仓库（workflow / bat / xml / conf / service）**没有任何 celery worker 或 beat 的启动或重启命令**。

→ P1-1 的 `W4` 是结构性的：不仅"需重启"，而是**当前部署流程根本没有重启/托管 worker 的入口**。

### 2.5 CI 在 main 上自动 migrate（`[已核实]`）

`.github/workflows/deploy.yml`：push 到 `main` → `git pull` → `pip install -r requirements.txt` → **`python manage.py migrate`** → 重启后端 → 健康检查 `/api/docs/`。

→ 这意味着**任何合并进 main 的 migration 会自动在生产执行**，且健康检查只看 Swagger 是否可访问（不校验数据迁移是否成功）。P1-2 的 migration 必须严格可加性、可回滚、且与旧代码二进制兼容。

### 2.6 `find_user_docs` 是唯一同时绕过权限helper与 status 门的读路径（`[已核实]`）

`utils/data_query.py:188-289`：`super_admin` 范围为 `KnowledgeDoc.objects.all()`（`:213-214`），**不过滤 `status`**，并在 `:259` 生成签名下载 URL 后于 `:277` 返回。经 `execute_query`（`:443`）→ `apps/chat/views.py:630` 的 `_handle_data_query` 可达。

### 2.7 FAQ 管线是 knowledge 内容在 knowledge app 之外的第二个消费者（`[已核实]`）

`apps/faq/tasks.py:265-268`：`KnowledgeDoc.objects.filter(source_type='website', visibility='public', status__in=['uploaded','indexed'])` → `:287` `extract_text_from_oss(...)` → 喂 LLM 生成 FAQ 草稿。

注意 selector 含 **`uploaded`**（尚未索引）——即"上传后未索引"的网站文档已经被消费。此路径不受 knowledge app 任何改动影响，**独立需要审核门**。

---

## 3. QUARANTINE_DESIGN（P1-2-B）

### 3.1 当前 OSS key 结构与隔离现状（`[已核实]`）

- 生成规则：`knowledge/{scope}/{owner_id}/{YYYY-MM}/{12hex}.{ext}`（`utils/oss.py:109-123`、`chunked_views.py:50-61`、`views.py:1317`）。
- `scope ∈ {school, college, department}`，`owner_id` 为 school=0 / college_id / department_id。
- 租户隔离**只体现在 key 前缀上**，bucket 只有一个（`OSS_BUCKET_NAME`），**没有按学院分桶、没有独立 bucket**。
- 爬虫用固定 `knowledge/website/0/...`。
- 现有 wrapper **没有** copy / move / 服务端读取能力（见 3.3）。

### 3.2 quarantine key 形态：推荐 `quarantine/` 顶层前缀（`[建议]`）

**推荐**：

```
quarantine/{scope}/{owner_id}/{YYYY-MM}/{uuid}.{ext}
```

即与 `knowledge/` **平级的独立顶层前缀**，而不是 `knowledge/quarantine/...`。

理由（全部基于当前代码事实）：

1. **把 `knowledge/` 从"来自上传链路"升级为"已通过审核"的安全不变量。** 现在 `check_indexable` 的 `INDEXABLE_OSS_KEY_PREFIX = 'knowledge/'`（`moderation.py:34`）对全部入口都放行，等于无效门。若 quarantine 对象物理上不在 `knowledge/` 下，这条已有检查**直接在 P1-2 变成真实的安全断言**，无需改其语义，也无需改动任何调用方。
2. **若用 `knowledge/quarantine/...` 会与现有校验冲突**：`UploadCallbackView._validate_oss_key_scope`（`views.py:811-837`）要求 key 以 `knowledge/{scope}/{owner_id}/` 开头，`knowledge/quarantine/...` 会被判为"不属于当前用户的上传范围"而拒绝；反过来若要放行就得放宽这个 P1-1 刚加的范围锁，属净损失。
3. **纵深防御**：即使 DB 的 `storage_state` 因并发/回滚而错误，未审核对象**物理上不在可索引命名空间**，`check_indexable` 仍会拒绝。
4. **成本可控**：`quarantine/` 与 `knowledge/` 同 bucket，promotion 用 OSS `copy_object` 服务端复制（不占本机带宽、不需下载到 Python 内存），复制后 `delete_object` 原对象。

> **[存疑/待人工确认]** promotion 采用 `copy + delete` 会有一个"复制成功、删除失败"的中间态（两份对象并存）。本报告在 §5 的 `promoting` 状态中按"记录 `promoted_key`，清理失败仅告警、不阻塞发布"处理。若要求"任一时刻仅一份对象"，则需改为 `copy → 校验 head → 标记 → 过期清理` 的两段式，并需要额外的回收任务。

### 3.3 正式对象 `knowledge/...` 的允许出现时机（`[建议]`）

```
upload
  ↓  (写入 quarantine/{scope}/{owner}/…)
quarantine
  ↓  (创建 ContentModeration: pending)
pending
  ↓  (扫描开始)
scanning
  ↓  (聚合结论 = clean)
approved
  ↓  (OSS 服务端 copy → knowledge/…，写 promoted_key)
promoting
  ↓  (head_object 校验新对象存在且 size/etag 一致)
promoted
  ↓  (index_document_task，门检查通过)
indexed
  ↓
RAG
```

**规则**：`knowledge/` 前缀下的对象 = **已通过审核并完成 promotion**。任何写入 `knowledge/` 的代码路径（E1–E9）在 P1-2 之后都必须先走 quarantine。`check_indexable` 的 `knowledge/` 前缀检查由此成为"确实通过了审核"的结构性证据。

### 3.4 OSS wrapper 能力逐项核对（`[已核实]`）

| 能力 | 现状 | 位置 | P1-2 所需接口（提案，本轮不实现） |
|------|------|------|--------------------------------|
| `head` | ✅ 有 | `utils/oss.py:253 head_object()`（三态返回，P1-1 加） | 复用；用于 promotion 后校验 |
| `delete` | ✅ 有 | `utils/oss.py:377 delete()` | 复用；用于 promotion 后删源、reject 后清理 |
| `stream`（服务端读内容） | ❌ **无** | — | **`iter_object_chunks(oss_key, chunk_size)` / `download_to_file(oss_key, path, max_bytes)`** —— 审核（ClamAV/LLM/Vision）必须拿到字节，当前后端**唯一**读内容的方式是 `get_download_url` + `ExternalClient.get` 后 `resp.content` **整文件进内存**（`extractor.py:274-277`、`views.py:1146-1148`） |
| `range` 读 | ❌ 无 | — | 可选：大文件分段/断点，建议不做，用流式串行读 |
| `copy` | ❌ **无** | （仅 `scripts/fix_oss_content_type.py:88` 直接用 `bucket.copy_object`，非 wrapper 方法） | **`copy_object(src_key, dst_key, content_type)`** —— promotion 的核心原语 |
| `move` | ❌ 无 | — | 不需要单独实现，= `copy` + `delete` |
| `list` | ⚠️ 间接（`bucket.list_parts`，`chunked_views.py:68`） | — | 不需要新增 |
| multipart | ⚠️ 间接（`bucket.*_multipart_upload`，`chunked_views.py:186,248,324`） | — | 不需要新增 |

**结论**：P1-2 必须新增两个原语 —— **服务端读内容（流式/落盘）** 与 **服务端复制**。这两个是 quarantine 方案的前置依赖，建议放在 P1-2B 第一个交付物，且必须带死代码级的最小实现（不引入新三方依赖，`oss2` 已具备 `get_object` / `copy_object` / `iter_content`）。

---

## 4. STORAGE_STATE_MACHINE（P1-2-C 之一）

`[建议]` 五态（采用题目给定集合，未增设）：

```
                  ┌─────────────┐
      upload ────► │ quarantine  │
                  └──────┬──────┘
                         │ moderation_state == approved
                         ▼
                  ┌─────────────┐   OSS copy 抛错 / head 校验不符
                  │  promoting  ├──────────────┐
                  └──────┬──────┘              │
                         │ copy+head 通过        │
                         ▼                     ▼
                  ┌─────────────┐      ┌────────────────────┐
                  │  promoted   │      │ promotion_failed   │
                  └──────┬──────┘      └─────────┬──────────┘
                         │                       │ 重试（次数受限）
                         │                       └──────► 回 promoting
                         │
                         │ doc 删除 / reject 清理
                         ▼
                  ┌─────────────┐
                  │   deleted   │
                  └─────────────┘
```

### 4.1 合法转移表

| 源 | 目标 | 触发者 | 条件 |
|----|------|--------|------|
| （无） | `quarantine` | upload pipeline | OSS 对象已写入 `quarantine/` 且 DB 记录已建 |
| `quarantine` | `promoting` | moderation 聚合结果 = `approved` | **CAS**：`UPDATE ... SET storage_state='promoting' WHERE id=? AND storage_state='quarantine'` |
| `promoting` | `promoted` | promotion 完成 | `copy_object` 成功 **且** `head_object(new_key)` 的 size 与源一致 |
| `promoting` | `promotion_failed` | copy/head 失败或超时 | 记 `promotion_error`，**进入有界重试** |
| `promotion_failed` | `promoting` | 重试任务 | `retry_count < MAX_PROMOTION_RETRY`（建议 3） |
| `promotion_failed` | `deleted` | 重试耗尽 | 清理 quarantine 对象，doc 标为不可用 |
| 任意 | `deleted` | 删除接口 / reject 清理 | 删除 OSS 对象（quarantine 与 promoted 各一） |

### 4.2 必须覆盖的边界

- **重试**：`promotion_failed → promoting` 有界（建议 3 次，指数退避）。耗尽后**不得**回退成 `quarantine`（否则会无限循环），应停在 `deleted` 或终态失败并告警。
- **并发**：`quarantine → promoting` 与 `deleted` 必须用条件 UPDATE（CAS）而非 read-modify-write。理由：`select_for_update()` 在 SQLite 上无效（§2.3），且在 MySQL 上也需要与 `transaction.atomic()` 同时使用；CAS 是跨库都正确的写法。
- **promotion 失败**：**绝不允许**因为 promotion 失败就"先建 KnowledgeDoc 再补发"—— 必须保持在 quarantine，不得出现在可读/可索引集合中。
- **OSS / DB 不一致**：以"DB 记录 + `head_object` 实测"双向校验。若 DB 说 `promoted` 但 `head_object(promoted_key)` 说不存在 → 降级为 `promotion_failed` 并重试（**幂等**：copy 覆盖同名 key 是安全的，因为 promoted key 由 UUID 决定、不会与他人冲突）。
- **删除**：删除必须同时处理 quarantine 与 promoted 两个 key；`KnowledgeDocDeleteView`（`views.py:310-316`）当前只删 `instance.oss_key`，P1-2 需要改为删除两个 key（或让 `oss_key` 始终指向 promoted、quarantine 由回收任务按 `storage_state` 清理）。
- **重复 callback**：E5 的重复登记已由 P1-1 的 `existing_by_key` 幂等（`views.py:719-727`）；P1-2 需保证重复回调**不会**把已 `promoted` 的文档退回 `quarantine`。

---

## 5. MODERATION_STATE_MACHINE（P1-2-C 之二）

`[建议]` 七态（采用题目给定集合）：

```
（无记录）──创建──► pending
                      │ worker 取任务（lease）
                      ▼
                   scanning
        ┌─────────────┼──────────────┬─────────────┐
        │             │              │             │
        ▼             ▼              ▼             ▼
    approved      rejected      unavailable      error
        │                                       │
        │ 用户显式确认（仅 unavailable）            │ 有界重试
        ▼                                       └──► scanning
  confirmed_unchecked
```

### 5.1 各状态允许的操作（`[建议]`）

| state | read | download | preview | index | RAG |
|-------|------|----------|---------|-------|-----|
| `pending` | 仅上传者本人（进度可见） | ❌ | ❌ | ❌ | ❌ |
| `scanning` | 同上 | ❌ | ❌ | ❌ | ❌ |
| `approved` | ✅（受范围过滤） | ✅ | ✅ | ✅ | ✅（还需 `status='indexed'`） |
| `confirmed_unchecked` | ✅（受范围过滤） | ✅ | ✅ | ✅ | ✅（同上） |
| `rejected` | ❌（对所有角色，含 super_admin） | ❌ | ❌ | ❌ | ❌ |
| `unavailable` | 仅上传者本人 + 审核管理员（用于决定是否确认） | ❌ | ❌ | ❌ | ❌ |
| `error` | 仅上传者本人（提示重试） | ❌ | ❌ | ❌ | ❌ |

**注**：`rejected` 对 super_admin 也 **不可读**（super_admin 可读全部内容用于运维的现有语义见 §15，但**审核拒绝必须对所有人一致**，否则闸门形同虚设）。若运维需要取证，走独立的、有审计的"待处置"接口，而不是普通读路径。

### 5.2 `unavailable ≠ approved` 的证明（设计要求强制）

- 二者是**不同的枚举值**，且集中判定函数必须显式列出白名单：`INDEXABLE_MODERATION_STATES = {'approved', 'confirmed_unchecked'}`，其余一律拒绝（黑名单式判断会被新状态绕过）。
- `unavailable` 的语义是"**无法完成审核**"（ClamAV/LLM/Vision/解析器不可用，或超时），因此它**没有任何检测器给出过 clean 结论**。把它等同 approved 等于"检测器宕机即全部放行"，这正是本设计要禁止的失败模式（§八 原则）。
- 因此 `unavailable` 在聚合优先级中**高于 `clean`**（见 §10）。
- 数据库层面：`unavailable` 的 `verdict` 只能是 `unavailable`，**不允许**为 `clean`。

### 5.3 `confirmed_unchecked` 的唯一合法来源（设计要求强制）

`confirmed_unchecked` 必须且只能由以下三者**同时**成立产生：

1. 当前 `moderation_state == 'unavailable'`（当前态，非历史态）；
2. 一次**显式的用户/管理员 API 调用**（非定时任务、非自动重试、非超时逻辑）；
3. 记录 `confirmed_by`（用户 FK）+ `confirmed_at` + `confirmation_reason`（非空）。

**明令禁止**的实现方式（必须在 code review 中作为拒绝项）：

- ❌ 超时后自动置 `confirmed_unchecked`；
- ❌ 重试次数耗尽后自动置 `confirmed_unchecked`；
- ❌ `scanning` 超过 N 小时自动置 `approved` / `confirmed_unchecked`；
- ❌ 任何定时任务（beat）写 `confirmed_unchecked`。

即：**"没人看" 永远不能变成 "可以看"**；只有"有人明确点了确认"才可以，且必须留痕。

---

## 6. VERDICT_MODEL（P1-2-D）

### 6.1 state 与 verdict 的职责划分

| 维度 | 含义 | 取值来源 | 谁关心 |
|------|------|---------|--------|
| `moderation_state` | **流程走到哪一步 / 是否可放行** | 状态机推进 | 访问控制、索引门、API |
| `moderation_verdict` | **内容是什么性质** | 检测器聚合结果 | 审核员、报表、申诉 |

二者**不可互相推导**，必须同时存在。典型组合：

| state | verdict | 含义 |
|-------|---------|------|
| `rejected` | `virus` | 检出病毒 |
| `rejected` | `harmful` | 有害内容 |
| `rejected` | `negative_guidance` | 负面指导 |
| `rejected` | `sensitive` | 敏感/涉密 |
| `rejected` | `mixed` | 多类同时命中（详见 §10） |
| `unavailable` | `unavailable` | 检测链未能完成 |
| `error` | `error` | 内部异常 |
| `approved` | `clean` | 全部检测器完成且均 clean |
| `confirmed_unchecked` | `unavailable` | **保留原 verdict**，不改成 clean —— 这点是刻意设计：确认只改变"是否放行"，不改变"内容性质记录" |

> `[建议]` `confirmed_unchecked` 保留 `verdict='unavailable'` 而非置为 `clean`，是为了让后续统计/复盘能区分"真的扫过且干净"与"人工放行未扫过"。

### 6.2 集合（采用题目给定集合）

`clean | virus | sensitive | harmful | negative_guidance | mixed | unavailable | error`

`mixed` 仅在**多个不同类别的拒绝级 verdict 同时成立**时使用（例如 rule=sensitive + LLM=harmful）；单类别多检测器命中不算 mixed。

---

## 7. CONTENT_MODERATION_MODEL（P1-2-E）

### 7.1 Q1：一对一 还是 一对多？

**推荐：两者都要，但职责分离** ——

```
KnowledgeDoc 1───1 ContentModeration        （当前态，门只读这一行）
                     │
                     └──1───N ModerationAttempt （每次尝试的历史，只写不改）
```

- `ContentModeration`：`OneToOneField(KnowledgeDoc)`。**唯一**被访问控制/索引门查询的对象（一次 JOIN 或一次 `select_related`，无需 `order_by` 取最新，避免"取最新"逻辑在并发下取错）。
- `ModerationAttempt`：`ForeignKey(ContentModeration)`，每次 `pending→scanning` 尝试写一行，**只追加不更新**（immutable audit log）。

理由：只做一对多 → 每个门都要 `order_by('-id').first()`，既有性能问题，也在"旧任务晚到"时容易取错（§7.3）；只做一对一 → 丢失历史，无法回答"这份文档被扫过几次、每次结论是什么"，也无法做审计与申诉。

### 7.2 Q2：是否需要保存历史审核结果？

**需要**。理由：① 申诉/取证需要"当时是谁、按什么策略、得出什么结论"；② 调试聚合逻辑需要看到每次尝试的原始 finding；③ `policy_version` 变化后要能区分"旧策略判 clean、新策略判 sensitive"。历史落在 `ModerationAttempt`，保留期建议 ≥ 180 天（`[建议]`，最终由业务定）。

### 7.3 Q3：重试如何避免旧任务覆盖新结果？

**单调序号 + CAS**：

- `ModerationAttempt.attempt_seq`（IntegerField，从 1 递增，`unique_together = (moderation, attempt_seq)`）。
- `ContentModeration.applied_seq`（IntegerField，记录已生效的序号）。
- 应用结果时用条件 UPDATE：

  ```sql
  UPDATE content_moderation
     SET state=%s, verdict=%s, applied_seq=%s, ...
   WHERE id=%s AND applied_seq < %s
  ```

  影响行数 = 0 表示"已有更新的结果生效"，**直接丢弃**本次结果（仅记日志）。
- 这样"旧任务晚到"天然被 `applied_seq < attempt_seq` 挡住，且不需要分布式锁。

> `[建议]` 不要用 `updated_at` 时间戳做比较 —— 同秒精度与时钟漂移都会出错；单调序号是唯一可靠判据。

### 7.4 Q4：如何实现幂等？

四层：

1. **同一 attempt 重复执行**：`ModerationAttempt (moderation, attempt_seq)` 唯一约束 → 重复插入被 DB 拒绝。
2. **同一 doc 重复提交审核**：入口先做 `get_or_create(ContentModeration, doc=...)`；若已是 `approved`/`rejected`（终态）则**不重开**（除非显式"重新审核"接口，且该接口必须递增 `policy_version` 并新建 attempt）。
3. **结果应用**：§7.3 的 CAS。
4. **promotion**：promoted key 由 UUID 决定且写入 `ContentModeration.promoted_key`；重复 promotion 用条件 UPDATE `WHERE storage_state='promoting'` 保证只执行一次；即便重复执行，`copy_object` 到同一目标 key 也是覆盖写、结果一致。

### 7.5 Q5：`pending` / `scanning` / `error` 之间的重试

`[建议]` 采用**租约（lease）**而非状态堆砌：

| 字段 | 作用 |
|------|------|
| `lease_owner` | 取任务的 worker/主机标识 |
| `lease_expires_at` | 租约到期时间 |
| `attempt_count` | 已尝试次数 |
| `next_retry_at` | 退避后下次可取时间 |

- 取任务：`UPDATE ... SET state='scanning', lease_owner=?, lease_expires_at=now()+N WHERE state='pending' AND (next_retry_at IS NULL OR next_retry_at<=now()) LIMIT 1` 语义（Django 用 `select_for_update(skip_locked=True)` + `atomic`，MySQL 8 支持 `SKIP LOCKED`）。
- **僵死恢复**：`state='scanning' AND lease_expires_at < now()` → 视为 worker 死亡，重置为 `pending` 或直接 `error`（视 `attempt_count`）。这是当前 P1-1 未覆盖的场景（`index_document_task` 没有僵死恢复）。
- `error` 的语义 = **内部异常且已耗尽重试**（不是"检测器不可用"）；重试由人工/管理员"重新审核"驱动，或由 `next_retry_at` 驱动的有界退避（建议 `attempt_count ≤ 3`，退避 30s/5min/30min）。
- **`unavailable` 不自动重试** —— 它需要外部依赖恢复；可由定时任务把 `unavailable` 且 `next_retry_at` 到期的记录重新置 `pending`（**这是允许的自动重试**），但**绝不能**改判为 `approved`。

### 7.6 字段清单（提案，本轮不写 models.py）

```
ContentModeration
  doc                 OneToOne  → KnowledgeDoc, on_delete=CASCADE
  state               CharField(16)  choices = pending/scanning/approved/rejected/unavailable/confirmed_unchecked/error
  verdict             CharField(24)  choices = clean/virus/sensitive/harmful/negative_guidance/mixed/unavailable/error
  categories          JSONField     去重后的类别标签列表（供列表筛选/报表）
  findings            JSONField     结构化 finding 列表（见 §8）
  checked_at          DateTimeField nullable  本次终态判定时间
  failure_reason      TextField     unavailable/error 的原因；pushed to user
  service             CharField     产生终态的主要服务标识（clamav/rule/llm/vision/aggregator）
  policy_version      CharField     策略版本，与 findings 一同决定结论可复现
  applied_seq         IntegerField  已生效的 attempt 序号（CAS 用）
  promoted_key        CharField     promotion 后的正式 key（空=未 promote）
  storage_state       CharField(20) choices = quarantine/promoting/promoted/promotion_failed/deleted
  promotion_error     TextField     promotion 失败原因
  promotion_retry     PositiveIntegerField
  quarantine_key      CharField     原始隔离 key（独立字段，勿复用 oss_key 语义）
  lease_owner         CharField     nullable
  lease_expires_at    DateTimeField nullable
  attempt_count       PositiveIntegerField
  next_retry_at       DateTimeField nullable
  confirmed_by        FK → User    nullable
  confirmed_at        DateTimeField nullable
  confirmation_reason TextField     nullable/blank
  reviewed_by         FK → User    nullable  人工终审（rejected 申诉撤销场景）
  created_at / updated_at

ModerationAttempt
  moderation          FK → ContentModeration, on_delete=CASCADE
  attempt_seq         IntegerField
  state_at_start      CharField
  started_at / finished_at
  detector_results    JSONField    各检测器原始结论（脱敏后）
  verdict             CharField
  error               TextField
  policy_version      CharField
  worker_id           CharField
  unique_together = (moderation, attempt_seq)
```

**为什么不把 `storage_state` / `moderation_state` 放回 `KnowledgeDoc`**：① 门只需要"是否可读/可索引"这一个联合判断，放 `ContentModeration` 一行可一次取回；② 避免再次出现 `is_sensitive`/`sensitive_checked` 那种"字段存在但语义不清、被当安全机制用"的问题；③ 保持 `KnowledgeDoc` 的 migration 影响最小（§29）。**但**若列表页需要按审核状态筛选，建议在 `KnowledgeDoc` 上加**只读派生字段**（由审核流程单向写入，`serializers.py` 标为 `read_only_fields`，与 P1-1 处理 `status` 的方式一致），避免列表页 JOIN。

---

## 8. FINDINGS_SCHEMA（P1-2-F）

### 8.1 结构（`[建议]`）

```json
{
  "category": "sensitive",
  "reason": "命中涉密关键词",
  "source": "rule",
  "location": {
    "kind": "page",           // page | paragraph | line | image | index | internal_path
    "value": 3
  },
  "excerpt": "……身份证号 5101**********23……",
  "confidence": 0.95,
  "rule_id": "classified_keyword:保密",
  "detector_version": "rule-2026.09"
}
```

字段语义：

| 字段 | 说明 |
|------|------|
| `category` | 与 verdict 同集合的类别标签：`virus/sensitive/harmful/negative_guidance/clean/unavailable/error` |
| `reason` | 人类可读的一句话，**不得回显原始敏感内容** |
| `source` | `clamav` \| `rule` \| `llm` \| `vision`（与现有实现对应关系见 §11-13） |
| `location.kind` | 至少覆盖 `page / paragraph / line / image / index / internal_path` |
| `location.value` | 页号 / 段号 / 行号 / 图片序号 / 分块 index / 归档内路径 |
| `excerpt` | **脱敏后**的最小上下文片段（见 8.2） |
| `confidence` | 0–1 浮点；规则命中可固定 1.0，LLM 用其自评 |
| `rule_id` / `detector_version` | 可复现性；策略变更后可重放 |

### 8.2 excerpt 的脱敏要求（强制）

**原则：findings 不得成为"泄露通道"。**

**现状差距（`[已核实]`）**：现有 `utils/sensitive_filter.py::check_sensitive` 返回 `matched_texts`（`sensitive_filter.py:198,226,238,250,261,270,281`）——**原样保存命中的敏感文本**；`apps/chat/models.py:109 SensitiveLog.matched_texts` 也是 JSONField 原样落库。若 P1-2 直接复用这个结构，**等于把敏感原文（含身份证号等）复制进审核记录表**，扩大了泄露面。必须新建脱敏层，**不要**直接复用 `matched_texts`。

脱敏规则（`[建议]`）：

1. 长度 > 40 字符的命中片段一律截断到 40 + `…`；
2. 结构化 PII（身份证/手机号/银行卡，对应 `PRIVACY_PATTERNS`）**中段掩码**：保留前 3 后 2；
3. **禁止**把整个文档段落或整个 LLM 上下文写进 `excerpt`；
4. 同一 finding 只保留一条最短命中片段，不保留全部匹配（避免长文本累积）。

### 8.3 其它必须回答的问题

- **JSONField 是否合适**：是。MySQL 8 原生支持 JSON；SQLite 测试后端亦支持。findings 是"随记录一起读写、不做字段级查询"的数据，无需独立表。若将来要对 finding 做筛选/统计，`categories` 已单独抽为顶层 JSONField + 建议加 `ContentModeration` 上的布尔/字符列做索引。
- **是否需要限制数量**：**必须**。建议 `MAX_FINDINGS = 50`，超出则截断并置 `findings_truncated: true`（否则一份命中上千个敏感词的文档会把 JSON 撑爆，且写放大严重）。
- **是否需要截断**：**必须**（见 8.2 第 1、3 条）。同时限制序列化后总 JSON 字节数（建议 ≤ 64KB）作为兜底。
- **是否需要脱敏**：**必须**（见 8.2）。
- **是否需要保存供应商原始 response**：`[建议] 默认不保存`；若确需，**不要**放同一张表的 JSONField，而应写入独立的、权限受限的表（或对象存储的受控前缀），仅存摘要 + `raw_ref` 指针。理由：LLM/Vision 原始返回包含被检测原文，等同第二份未脱敏副本；放进 findings 会让"审核记录"退化成"敏感内容副本"。

---

## 9. CONTENT_EXTRACTION（P1-2-G）

### 9.1 当前解析路径实测（`[已核实]`，`apps/knowledge/extractor.py`）

| 类型 | 解析器 | 实现方式 | 状态 |
|------|--------|---------|------|
| PDF | `_parse_pdf` `:49` | 先 `fitz`(PyMuPDF)，回退 `pdfplumber` | ✅ 支持（**两个库均未在 requirements.txt 声明**） |
| DOCX | `_parse_docx` `:76` | `python-docx`（段落 + 表格） | ✅ 支持（未声明依赖） |
| PPT/PPTX | `_parse_pptx` `:98` | `python-pptx` | ⚠️ `.ppt` 走同一函数但 python-pptx **不能解析旧版二进制 .ppt** → 返回 `''` |
| XLSX | `_parse_xlsx` `:114` | `openpyxl` | ✅（未声明） |
| XLS | `_parse_xls` `:152` | `xlrd`（ImportError → 明确告警） | ⚠️ 依赖 `xlrd`，未声明 |
| CSV | `_parse_csv` `:134` | 标准库，多编码回退 | ✅ |
| TXT/MD/HTML/HTM | lambda `:233-236` | 直接按 UTF-8 读 | ✅ |
| DOC（旧） | `_parse_doc` `:177` | ① `subprocess.run(['antiword', ...])` ② `docx2txt` ③ `subprocess.run(['soffice', '-–headless', ...])` | ⚠️ **执行外部程序**；且方案③**不可达**（方案②的 `except ImportError` 直接 `return ''`，`:192-194`） |
| 图片 (png/jpg/…) | 无 | 回退到"当作文本读"分支 `:282-284` | ❌ 无提取（P1-1 的 Vision 只在**上传时**用于生成元数据，不入索引） |
| 音视频 | 无 | 同上 | ❌ 无提取（`utils/asr.py` 存在但知识库文档链路未接） |
| ZIP / RAR / 7Z | **无** | 回退到"当作文本读"分支 `:282-284` | ❌ 无提取，且**不报错** |

### 9.2 必须回答的风险问题

**Q：哪些可能执行外部程序？**
A：`_parse_doc`（`extractor.py:182` `antiword`、`:208` `soffice`）。另有 `utils/asr.py` 走 `ffmpeg`（经 `imageio-ffmpeg`/`pydub`）、`utils/js_renderer.py` 走 Playwright（爬虫用）。**P1-2 的审核必须在执行这些解析器之前完成病毒扫描**（§12）。

**Q：哪些可能造成 zip bomb / decompression bomb？**
A：**当前没有归档解析，所以当前不构成风险**；但正因如此，`zip/rar/7z` 落入了"按 UTF-8 逐字节当文本读"的分支。**这是一个真实缺陷**：`extract_text_from_oss` 对未知扩展名返回 `errors='ignore'` 解码出的乱码字符串，`len(...)>0`，于是 `index_document_task` 判定"提取成功"并**把乱码写入 DocChunk + Milvus**（`tasks.py:174-181` 只判断 `not text.strip()`）。即：**解析失败被当成解析成功**。
→ P1-2 若新增归档支持，必须同时实现：最大解压后总量、最大条目数、最大嵌套深度、单条目大小上限、**禁止解压到符号链接/绝对路径**。

**Q：哪些可能导致内存爆炸？**
A：多处，且都在当前代码里：
1. `extractor.py:274-277`：`ExternalClient.get(download_url)` 后 `resp.content` —— **整个对象进内存**，最高 500MB（视频档）。
2. `views.py:1320`（FileUploadView）、`views.py:1617`（BatchFileUploadView）：`uploaded_file.read()` —— **整个上传文件进内存**。
3. `views.py:1148`（`_text_preview`）：`resp.text` 整文件进内存。
4. `utils/oss.py:304-330` `compute_md5`：虽然分块读，但上传路径用不到它（`views.py:1321` 用 `hashlib.md5(file_bytes)`）。
5. `_parse_xlsx` 无行数上限；`chunk_document` 无输入长度上限（`apps/knowledge/utils.py:17-72`），超长文本 → 巨量 chunk → 一次 `llm_client.embed(texts)` 全量送入。

**Q：哪些可能执行宏/脚本？**
A：`antiword` / `soffice` 打开文档时可能触及宏，但**当前调用未加 `--safe-mode`/禁用宏参数**（`extractor.py:208-211`）。`docx2txt`、`openpyxl(data_only=True)`、`python-docx`、`python-pptx` 均为纯解析、不执行宏。**未发现任何 `eval`/`exec`/`pickle.load` 于解析链路上**。
→ 设计上应假设"解析器是不可信输入的攻击面"，并在隔离进程 + 超时 + 资源限制下运行（§34 DEPLOYMENT）。

### 9.3 未来统一 `extract_content()` 的设计约束（`[建议]`，本轮不实现）

**核心不变量：解析失败 / 提取为空 / 依赖缺失 → `unavailable` 或 `error`，绝不允许是 `clean`。**

```
extract_content(oss_key, file_type, file_name) -> ExtractResult
    status:  ok | unsupported | empty | dependency_missing | failed
    text:    str            （status=ok 时非空）
    pages:   [{page:int, text:str}]         （有页概念的格式）
    images:  [{index:int, bytes|ref:...}]   （供 Vision 检测）
    units:   [{kind:'paragraph'|'line', index:int, text:str}]  （供 location 定位）
    reason:  str            （status != ok 时）
```

映射规则：

| ExtractResult.status | → moderation | 说明 |
|----------------------|--------------|------|
| `ok` | 继续后续检测器 | — |
| `unsupported` | `unavailable` | 当前 png/mp3/zip 属于此类，**不能再当成"文本已提取"** |
| `empty` | `unavailable` | 0 字节 / 提取后为空 |
| `dependency_missing` | `unavailable` | 当前 `xlrd`/`docx2txt`/`antiword` 缺失即属此类 |
| `failed` | `error` | 解析器抛异常 |

同时必须修正 `extract_text_from_oss` 的契约：新增"未知扩展名 → 返回 `unsupported`"而非"当文本读"，否则乱码入库问题会延续到 P1-2。

---

## 10. CLAMAV（P1-2-H）

### 10.1 位置：**在 extract 之前**（`[建议]`）

```
quarantine 对象
   ↓  ① ClamAV 扫描（直接读原始字节，不经任何解析器）
   ↓  ② extract_content()
   ↓  ③ rule / LLM / Vision
```

理由（基于当前代码事实）：`extract_content` 会调用 `antiword` / `soffice`（`extractor.py:182,208`）等**外部程序**并执行解析库。**不应先把可能含毒的文档交给文档解析器**。先扫后解，可把"恶意样本触发解析器漏洞"的风险挡在最前面。

### 10.2 必须满足的不变量

- `virus` 命中 → `state=rejected, verdict=virus` → **不 promote、不 index、不 RAG、不可读**（§15）。
- **ClamAV 不可用 → `unavailable`，绝不 `approved`**。这是本设计最容易被写错的一点（"扫描失败了就放行"）。

### 10.3 设计要素（`[建议]`）

| 要素 | 设计 |
|------|------|
| 协议 | clamd `INSTREAM`（TCP，19200）或 `clamscan`（进程）。**推荐 clamd INSTREAM**：无需落盘、可流式、可设超时 |
| timeout | 连接 5s、单次流式写入空闲 30s、整体 300s（与 `CELERY_TASK_TIME_LIMIT=300` 对齐，见 `settings.py:221`） |
| retry | 有界 3 次；区分"传输错误（可重试）"与"引擎错误（不可重试）" |
| circuit breaker | **复用现有 `utils/http_client.py::CircuitBreaker`**（`http_client.py:42-72`，threshold=5, recovery=60s）。熔断打开时**直接判 `unavailable`**，不阻塞上传线程 |
| large file streaming | 从 OSS 流式读 → 分块喂 INSTREAM，**不在 Python 内存里聚合整个文件**；需要 §3.4 的 `iter_object_chunks` |
| service unavailable | 连接被拒 / 熔断开 / 引擎报错 → `unavailable` + `failure_reason` |
| result normalization | clamd 返回 `FOUND`（含签名名，如 `Eicar-Test-Signature`）/ `OK`；归一化为 `{verdict: virus|clean, signature: str}`；**未知/空响应 → `unavailable`**，不得默认 clean |
| EICAR | 用 `Eicar-Test-Signature` 做端到端测试（不引入真实病毒） |
| 大小超限 | clamd `StreamMaxLength` 默认 25MB，**必须调大或走临时文件模式**，否则大文件会被判 `INSTREAM size limit exceeded` → 若不处理会变成 `error`（可接受，但需明确而不是误判 clean） |

### 10.4 部署形态（`[建议]`）

ClamAV 是**外部服务/系统依赖**，不是 Python 包。P1-2 需要决定：本机 clamd（`clamav-daemon` + `freshclam` 签名更新）还是远程扫描服务。前者需要服务器常驻进程与内存（`ClamAV` 特征库约 1–2GB 常驻），**必须在 P1-2I 明确评估服务器资源**。

---

## 11. RULE_ENGINE（P1-2-I 之一）

### 11.1 现状可复用度（`[已核实]`）

`utils/sensitive_filter.py` 已有成熟四路规则：

| 路 | 名称 | 数据 | 说明 |
|----|------|------|------|
| 1 | 涉密保密关键词 | `CLASSIFIED_KEYWORDS` `:20` | 子串匹配 |
| 2 | 个人隐私正则 | `PRIVACY_PATTERNS` `:42` | `re.findall` |
| 3 | 结构化保密数据 | `STRUCTURAL_SECRET_PATTERNS` `:93` | 正则 |
| 4 | 保密特殊逻辑 | `FORBIDDEN_DOC_GENERATION` `:135` / `FORBIDDEN_ACCESS_ACTIONS` `:141` / `BYPASS_ATTEMPT_PATTERNS` `:148` | 子串匹配 |

输出 `SensitiveResult(is_sensitive, level, matched_rules, matched_texts, refuse_message)`（`:192-199`），并已按 `type` 优先级选拒绝话术（`:290-297`）。

### 11.2 用于文档审核的两处不足（`[已核实]`）

1. **无定位信息**：只接受 `text`（`:202`），返回的是全文命中的规则名与片段，**没有 page/paragraph/line**。findings 的 `location` 无法直接从它得到。
   → `[建议]` 新增包装层按 page/paragraph 切段调用 `check_sensitive`，把段的定位带进 finding；**不要**改 `check_sensitive` 的签名（它服务聊天链路，改动风险外溢）。
2. **`matched_texts` 是原文**（§8.2）：文档审核必须走脱敏层后再落库，不能直接把 `matched_texts` 写进 findings。

### 11.3 适合的范围（`[建议]`）

`敏感词 / 明显违规模式 / 文件名 / 路径`。特别是**文件名与路径**：`file_name` 与 `oss_key` 是当前的薄弱面（`normalize_file_type`、`get_file_ext` 只做展示层解析），规则引擎应把文件名纳入检测输入。

---

## 12. LLM（P1-2-I 之二）

- **可复用**：`utils/llm.py::LLMClient.chat()`（`:278`），已带重试与超时（`http_client.TIMEOUT_PROFILES['llm_chat'] = (10, 60)`）。
- **适合**：文本语义、危险指导、负面指导、复杂上下文。
- **注意（现状事实）**：
  - `llm_client.chat` 失败返回 `_mock_chat` 或空（`:556`、`:401` 的流式同理）。**必须区分"mock 降级"与"真实结论"** —— 若环境变量缺失导致走了 mock，审核不得据此判 clean。`[存疑/待人工确认]` mock 触发的确切条件需在实现阶段逐一核对，不得凭猜测定策略。
  - 现有 `_ai_classify`（`views.py:1816`）已经在**上传时**把内容喂给 LLM 生成元数据，**且其返回的 `visibility` 会直接影响文档可见性**（`views.py:1422`）。这意味着"未审核内容"已经被发送给外部 LLM —— P1-2 要明确这是否可接受，以及是否要在审核前先拦住。**本轮只记录，不修（§二）**。
- **不可作为唯一判据**：LLM unavailable ≠ clean（§10.2 同理）。

---

## 13. VISION（P1-2-I 之三）

- **可复用**：`utils/llm.py::chat_vision`（`:334`）+ `image_bytes_to_data_url`（`:74`，含 Pillow 压缩到 1280px / quality 85 —— **已有内存保护**）。
- **超时档案**：`TIMEOUT_PROFILES['llm_vision'] = (10, 120)`（`http_client.py:29`），比文本模型宽。
- **适合**：图片、PDF embedded image、扫描件、图片型证书。
- **现状**：Vision **仅用于上传时的元数据生成**（`views.py:1857-1878`），**不参与内容审核**；且只对 `IMAGE_FILE_TYPES`（`serializers.py:255`）生效。PDF 内嵌图片的抽取**完全没有实现**（`_parse_pdf` 只取文本层）。
- **设计要点**：Vision 检测需要"图片清单"，而 `extract_content` 当前不产出图片（§9.3 已把它列入设计）；PDF 扫描件（无文本层）在现状下会提取为空 → `tasks.py:175` 判 `failed`，**不是** `unavailable`。P1-2 需把这条改为 `unavailable`（因为"扫不出来"恰恰是最需要 Vision 的场景，而不是"内容为空"）。

### 13.1 多检测器结果汇总（题目要求明确）

`[建议]` 三者**并行独立**执行，各自产出 findings，**不做短路**（不因 rule 命中就跳过 LLM/Vision）。理由：① 短路会丢失"同时命中病毒"这类更高危信号；② findings 需要完整以便申诉；③ 检测成本远低于漏检代价。但对**超大文件**可做受限执行（§14.2）。

---

## 14. RESULT_AGGREGATION（P1-2-J）

### 14.1 推荐优先级（含理由）

```
virus            ┐
harmful          │  拒绝级（Deny）
negative_guidance│
sensitive        ┘
        >
unavailable      ┐  不可放行级（Block-without-verdict）
error            ┘
        >
clean            放行级（Allow）
```

即：**先取"是否存在任一拒绝级 verdict"；若有 → rejected。否则若"是否存在任一 unavailable/error" → 对应状态。否则 → approved(clean)。**

理由（结合本系统业务风险）：

1. **`virus` 最高**：不可争辩、非"内容观点"问题、处置成本最低（重新上传即可），且 `rejected` + 不可读的代价最小。
2. **`harmful` > `negative_guidance`**：`harmful` 指可直接造成伤害的内容（暴力/危险操作），`negative_guidance` 指倾向性/引导性内容。前者后果更直接。
3. **`sensitive` 在拒绝级中最低但仍属拒绝**：本系统的 `sensitive` 语义是"涉密/个人隐私"（`sensitive_filter.py:20,42,93`），**是合规硬要求，不能在校园场景下放行**；但它的误报率高于前三者，因此排在最后（先让更高危的 verdict 决定 verdict 字段，sensitive 仍导致 rejected）。
4. **`unavailable` / `error` 高于 `clean`**：这是本题的核心要求 —— **"没扫成"必须比"扫出来是干净的"更严格**。把 unavailable 排在 clean 之下（即"clean 优先"）会让检测器宕机成为放行后门。

### 14.2 mixed 的处理

- 多个**不同类别**的拒绝级 verdict 同时成立 → `verdict='mixed'`，但 `categories` 保留全部类别，`state='rejected'`。
- 同一类别多个检测器命中（rule=sensitive + LLM=sensitive）→ **不算 mixed**，`verdict='sensitive'`。
- 优先级**只用于决定单值 `verdict`**；`state` 的判定始终是"拒绝级任一存在即 rejected"。

### 14.3 题目给出的两个例子

**例 1**：`rule = sensitive`，`LLM = clean`，`virus = clean`
→ 存在拒绝级 `sensitive` ⇒ `state=rejected, verdict=sensitive, categories=['sensitive'], findings=[rule 的 finding]`。**不放行**。✅ 不会因"两票 clean 一票 sensitive"而放行（不是多数表决）。

**例 2**：`LLM unavailable`，`Vision unavailable`，`Rule clean`
→ 无拒绝级；存在 unavailable ⇒ `state=unavailable, verdict=unavailable`。**不放行**，进入"仅上传者与审核管理员可见 + 可被显式确认"的流程。✅ 不会得出 `approved`。

---

## 15. UPLOAD_PIPELINE（P1-2-K）

### 15.1 提议模块 `apps/knowledge/upload_pipeline.py`（本轮不创建）

```
upload
  ↓  1. 接收 + 基础校验（类型白名单 / 声明大小 / 归属 / 权限）
  ↓  2. 写入 quarantine/{scope}/{owner}/…（或对 STS/分片：校验最终对象在 quarantine 前缀下）
  ↓  3. 建 KnowledgeDoc（status='uploaded'）+ ContentModeration(state='pending', storage_state='quarantine')
  ↓  4. extract_content()                      ← 失败/空/缺依赖 → unavailable / error（§9.3）
  ↓  5. ClamAV（原始字节，先于解析器）
  ↓  6. rule
  ↓  7. LLM / Vision（并行，不短路）
  ↓  8. aggregate（§14）
  ↓  9. approved?  ──否──► rejected / unavailable / error（不 promote、不 index）
                     │是
                     ▼
                 10. promotion（copy → knowledge/…，head 校验）
                     ▼
                 11. storage_state='promoted'
                     ▼
                 12. index_document_task（任务体内复核 moderation + storage）
                     ▼
                 13. DocChunk + Milvus  ⇒ RAG
```

### 15.2 各上传形态的差异（必须分别设计）

**(a) 普通上传 `FileUploadView`（E1）**
现状：后端先 `put_object` 到 `knowledge/`（`views.py:1332`）再建 doc 再索引。P1-2 需改为写 `quarantine/`，并**取消"上传即索引"**，改为"审核通过后由 pipeline 触发索引"。
另需修掉"整文件进内存"（`views.py:1320`）—— 用分块读 + `put_object` 流式（`oss2` 支持文件对象）。

**(b) 批量上传 `BatchFileUploadView`（E2）**
现状已满足"单个失败不阻塞其他"的结构（`views.py:1588-1740` 逐文件 `try/except` + `continue`）。`[建议]` 保持该结构，但注意：
- OSS 写成功而 `KnowledgeDoc.objects.create`（`:1672`）失败会留下**孤儿对象**（当前无补偿，§24）。
- 若改为"审核通过后索引"，批量接口的返回语义需明确为"**已受理**"而非"已入库"，`success_count` 的口径要重新定义（否则前端会以为已可检索）。

**(c) 文本上传 `TextUploadView`（E3）**
**结论：文本上传必须走统一 moderation。** 现状它直接 `upload_text` → 建 doc → 索引（`views.py:1002,1024,1057`），且 `content` 字段**没有任何长度上限**（`TextUploadSerializer.content` 为 `CharField` 无 `max_length`，`serializers.py:358`），`file_size` 由 `len(content.encode())` 计算（`:1032`）。
文本是最容易被直接注入违规内容的入口，且**不需要 ClamAV 意义上的病毒扫描**，但仍需 rule + LLM。设计上建议：文本走同一 `ContentModeration`，`storage_state` 仍用 quarantine→promote（保持一致性），`verdict` 只可能来自 rule/llm。

**(d) STS（E4）**
**必须 `STS credentials only write quarantine`。**
现状（`[已核实]`）：`get_sts_credentials`（`oss.py:128-179`）**生产分支的 STS 代码整段被注释掉**（`:155-168`），实际返回的是 `sign_url('PUT', oss_key, 300)`（`:169`），即**用一个可写整个对象的签名 URL**替代 STS；注释里原计划的 policy 也写的是 `knowledge/...` 前缀（`:163`）。
设计要点：
- 签发时 key 直接生成在 `quarantine/{scope}/{owner}/{YYYY-MM}/…`；
- 若启用真实 STS，policy 的 `Resource` 必须收紧到 `quarantine/{scope}/{owner_id}/*`，且**不含** `knowledge/*`；
- `dev_mode=True` 的签名 URL（`:145`）同样只能签 quarantine 路径；
- **回调登记时必须校验 key 确实落在 quarantine 前缀**，并沿用 P1-1 的 `head_object` + scope 校验（`views.py:811-907`）。

**(e) Callback（E5）**
**必须重新确认五件事**（题目要求）：`server-issued upload intent` / `actual object metadata` / `size` / `type` / `hash` / `ownership`。

现状逐项（`[已核实]`）：

| 项 | 现状 | 缺口 |
|----|------|------|
| server-issued upload intent | ❌ **无** | STS 签发的 key 未落库，回调时无法证明"这个 key 是我签发的" |
| actual object metadata | ✅ P1-1 `head_object`（`views.py:870`） | — |
| size | ✅ 以 OSS 实测为准（`views.py:877-882`） | — |
| type | ✅ Content-Type 与后缀一致性（`views.py:891-898`） | — |
| hash | ⚠️ 仅当 ETag 是 32 位十六进制时比对（`views.py:902-905`），分片 ETag 形如 `<md5>-<n>` 跳过 | 分片场景无 hash 校验（P1-2 可由 `ChunkedUploadSession.file_md5` + 实测大小部分覆盖） |
| ownership | ❌ **BLOCKED**（P1-1 已披露） | 需要服务端上传意图记录 |

**→ 这正是题目所指的 `PendingUpload / UploadSession` 需求。** `[建议]` P1-2 引入 `UploadIntent`（或复用并扩展 `ChunkedUploadSession`）：STS/init 时落库 `{oss_key, user, declared_name, declared_size, md5, expires_at, used_at}`，回调时要求 `oss_key` 命中一条**未使用**的 intent 且 `user` 一致，用后置 `used_at`。这同时解决"重复 callback"与"对象认领"两个问题。
`[建议]` 该改动放在 P1-2 的**独立阶段**（因为它是 BLOCKED 项，风险与工作量都独立），不要与 moderation 主体混在一个 commit。

**(f) Chunked（E6-E8）**
**必须 `chunks → merge → final object moderation`。**
- ❌ 不对每个 chunk 单独审核（chunk 是字节碎片，无语义；且会放大 N 倍成本）；
- ❌ merge 后**不得**直接 index（现状 `chunked_views.py:398` 正是直接 `safe_delay(index_document_task, ...)`）；
- ✅ merge 完成的**最终对象**进入 moderation，通过后 promote 再 index。
- 现状可复用：merge 已在事务 + `select_for_update` 内（`chunked_views.py:281-392`），`status='uploading'` 检查保证幂等（`:287`），实测大小校验（`:306-320`）。P1-2 只需把"建 doc + 触发索引"替换为"建 doc(quarantine) + 提交审核"。
- 注意：`oss_key` 由 init 时生成（`_build_oss_key`，`:184`），P1-2 需改为 quarantine 前缀，且 `complete_multipart_upload` **不能改变对象 key** —— 因此 quarantine 前缀必须在 init 时就确定，这一条要在 `UploadIntent` 里一并落库。

---

## 16. LARGE_FILE（P1-2-L）

### 16.1 现状限制（`[已核实]`）

| 限制 | 值 | 位置 |
|------|-----|------|
| 分类型最大上传 | 30MB–500MB（`FILE_SIZE_LIMITS`） | `serializers.py:100-128` |
| 绝对上限（合并后） | 500MB（= 视频档 = `max(FILE_SIZE_LIMITS.values())`） | `serializers.py:135` |
| 单片上限 | 500MB（= `MAX_FILE_SIZE`） | `serializers.py:136` |
| nginx 上传体积 | `client_max_body_size 500m`（仅 `/api/knowledge/upload/`） | `deploy/nginx.conf` |
| Django 内存阈值 | `DATA_UPLOAD_MAX_MEMORY_SIZE` / `FILE_UPLOAD_MAX_MEMORY_SIZE` = 100MB | `settings.py:113-114` |
| 分片数 | 0–9999（`chunked_views.py:230`） | — |
| 索引任务时限 | `CELERY_TASK_TIME_LIMIT=300s` / `SOFT=240s` | `settings.py:221-222` |

### 16.2 分档策略（`[建议]`）

| 档 | 范围 | 处理 |
|----|------|------|
| small | ≤ 32MB | 可一次性读入（但仍建议走流式，避免回退到现状的整读）；ClamAV INSTREAM 直传 |
| large | 32MB – 500MB | **必须流式**：`iter_object_chunks` → clamd INSTREAM；解析走"落临时文件 + 解析器读路径"（现状 `extract_text_from_oss` 已是落盘临时文件，可复用），临时文件需限时清理与磁盘配额 |
| very large | > 500MB | **拒绝**（当前上游 nginx 也是 500m，一致），返回明确错误码 |

**硬性要求：不得把整个文件一次性读进 Python 内存。** 需要修的三处已在 §9.2 列出（`extractor.py:277`、`views.py:1320`、`views.py:1617`），其中前两处在 P1-2 的审核链路上，必须一起改。

### 16.3 需新增的限额（`[建议]`，全部为 P1-2 新引入）

| 限额 | 建议值 | 理由 |
|------|--------|------|
| 最大文件大小 | 500MB（保持现状） | 与 nginx/前端契约一致，不破坏兼容 |
| **最大解压后大小** | 200MB | 归档炸弹防护（若支持归档） |
| **最大归档条目数** | 1000 | 同上 |
| **最大嵌套深度** | 2 | 同上 |
| **最大文本长度** | 2,000,000 字符 | 现状**无任何上限**（`chunk_document` 无限制，`utils.py:17`）；超限则**截断并记 finding**，而不是失败 |
| **最大图片数量** | 50 | Vision 成本控制 |
| **最大 OCR/Vision 页数** | 50 | 同上 |
| **单次审核时间预算** | ≤ 240s | 与 `CELERY_TASK_SOFT_TIME_LIMIT` 对齐，超时 → `unavailable`（**不是 error，也不是 approved**） |

**与 500MB 的对齐方式**：500MB 是**存储/上传**上限，保持不变；审核侧新增的限额是**处理**上限。二者冲突时（如 500MB 的 PDF 超过 OCR 页数上限）：**只对超限的那一路检测器判 `unavailable`**，其余检测器仍完成；聚合时因存在 unavailable ⇒ 该文档**不会被自动放行**（§14）。这样既不破坏上传兼容性，也不会因为"文件太大"而静默放行。

---

## 17. ACCESS_CONTROL（P1-2-M）

### 17.1 门应加在哪一层

`[建议]` 新增**集中式只读判据**（与 P1-1 把 `check_indexable` 放任务体同一思路）：

```
def readable_docs(queryset) -> QuerySet:
    return queryset.filter(
        content_moderation__storage_state='promoted',
        content_moderation__state__in=('approved', 'confirmed_unchecked'),
    )
```

并把它**组合进**已有的 `_admin_scope_filter` / `_rag_scope_filter` / `authorized_knowledge_docs`，**不**在 3 个视图里各写一遍。

### 17.2 必须逐一加门的读路径（`[已核实]` 现状）

| # | 路径 | 现状门 | 需要的门 |
|---|------|--------|---------|
| 1 | `KnowledgeDocListView.get_queryset`（`views.py:118`） | `_admin_scope_filter` | + `readable_docs` |
| 2 | `KnowledgeDocDetailView`（`views.py:256`）→ **`KnowledgeDocSerializer.get_download_url` / `get_oss_url`（`serializers.py:50-62`，7 天有效期）** | `_admin_scope_filter` | + `readable_docs`；**并考虑未通过时返回空串** |
| 3 | `KnowledgeDocDownloadView`（`views.py:423-432`） | `_admin_scope_filter` | + `readable_docs` |
| 4 | `KnowledgeDocPreviewView`（`views.py:1101-1106`、`1132`、`1144`） | `_admin_scope_filter` | + `readable_docs` |
| 5 | `utils/data_query.py::find_user_docs`（`:188`，签名 URL 在 `:259`） | **仅内联角色范围** | + `readable_docs`；super_admin 也需要 |
| 6 | `utils/data_query.py::_published_docs_queryset`（`:291-295`） | `status='indexed'` | + `readable_docs`（统计口径） |
| 7 | `utils/sse.py::build_references_events`（`:231`） | `authorized + status='indexed'` | + `readable_docs` |
| 8 | `utils/sse.py::build_images_events`（`:321` 签名 URL） | 来自 `retrieve_images`（`rag.py:1042`：`authorized + image + indexed`） | + `readable_docs`；**注意该出口对匿名用户也开放**（`AskView` 为 `AllowAny`） |
| 9 | `utils/rag.py` 8 处 `status='indexed'` 过滤（`:581,630,657,856,896,918,980,1042`） | `authorized_knowledge_docs + status='indexed'` | + `readable_docs`（加在 `authorized_knowledge_docs` 内最省事，但会同时影响 `data_query`，需评估） |
| 10 | `apps/knowledge/serializers.py` 列表序列化器 | 无 URL 字段（**已核实无泄露**） | 无需改 |
| 11 | `apps/faq/tasks.py:265-268`（内容消费，非展示） | `source_type/visibility/status` | + `readable_docs` |

### 17.3 signed URL 是否可能在 moderation 之前泄露？

**是，当前会。** `[已核实]` 三个签 URL 的 REST 出口（`serializers.py:54/62`、`views.py:430`、`views.py:1132/1144`）**只受 `_admin_scope_filter` 约束，完全不过滤 `status`**，更不过滤任何审核状态：

- `KnowledgeDocDetailView` 的 GET 会在响应的 `download_url` / `oss_url` 里带上 **7 天有效期**（`expires=604800`，`serializers.py:55,62`）的签名 URL；
- 因此 **只要一个审核中的文档落在调用者的管理范围内（或就是本人上传），它就已经可被下载与预览**。

→ 结论与对策：**quarantine 若只改 OSS 前缀而不改这三个出口，等于没有隔离** —— 因为签名 URL 签的是 quarantine 对象本身。必须同时：① 前缀迁移；② §17.2 全表加门；③ 审核未通过时**不返回**任何签名 URL（返回空串 / 不输出该字段），而不是返回一个指向 quarantine 的 URL。

---

## 18. INDEX_GATE（P1-2-N）

### 18.1 现状（`[已核实]`）

`check_indexable`（`moderation.py:37`）已在**任务体内**（`tasks.py:147`），位置正确；`moderation.py:5-16` 的注释准确枚举了 6 条到达路径。**所有生产入口都收敛到任务体**（已逐条核实：E1–E9 的 6 个 `safe_delay` + `crawler.py:491` + `config/celery.py:89` + 两个脚本的 `.run(None, ...)`）。

### 18.2 存在的 bypass（`[已核实]`）

| # | 路径 | 性质 |
|---|------|------|
| B-1 | `scripts/seed_test_docs.py::index_seed_docs`（`:857-959`） | **真正的 bypass**：自行写 DocChunk + Milvus，不调任务（§2.1） |
| B-2 | `scripts/rebuild_milvus.py:38` | `Collection(...).drop()` —— 破坏性，非注入，但无门 |
| B-3 | `safe_delay` 双重吞异常（`celery_dispatcher.py:32-49`） | 任务可能**静默不执行**（§2.2） |
| B-4 | `chunked_views.py:42` `from .tasks import ... index_document_task` + `:398` | 模块级绑定；**patch 定义处无效**（P1-1 已知坑，测试必须 patch 使用处） |
| B-5 | 无 celery 时 `shared_task` 丢弃 `bind/max_retries` 且**不设 `.name`**（`shared_task_compat.py:46-81`） | `safe_delay` 的 `getattr(task,'name',task)` 退化为函数对象；`max_retries` 退化为 0（`tasks.py:159`） |

### 18.3 设计要求（`[建议]`）

> **无论 task 从哪里进入，最终 task body 都再次检查 moderation/storage。**

- 保持闸门在 `tasks.py` 任务体**开头**，且**升级为读写两项**：`storage_state == 'promoted'` **且** `state ∈ {approved, confirmed_unchecked}`。
- 沿用 P1-1 的"写状态而非抛异常"约定（避免 §2.2 的静默跳过）：不通过时写 `status='failed'` + `index_error`，**不抛异常**。
- crawler（`crawler.py:491`）、management command（`crawl_website.py`）、`config/celery.py:89` 的转发任务**已经**自动收敛，无需各自加门 —— 这正是把门放任务体的价值。
- **必须处理 B-1**：让 `index_seed_docs` 改为调用任务，或在脚本内显式调用同一闸门函数（且脚本仅用于本地/测试，不应在生产运行）。
- **建议增加**：`DocChunk`/Milvus 的写入收敛为**单一函数**（目前有 3 处写入：任务体、seed 脚本、cascade 删除），否则未来仍会有新 bypass。

---

## 19. RAG（RAG 专章）

### 19.1 现状（`[已核实]`，来自本轮只读审计）

- 入口：`utils/rag.py::retrieve()` `:1093`（被 `apps/chat/views.py:451` 与 `:1039` 调用）；另有独立的 `retrieve_images()` `:999`（被 `:484` 调用）—— **两条独立入口**。
- 权限 helper：`utils/rag.py::authorized_knowledge_docs(user_context)` `:808`。**`super_admin` 直接 `return KnowledgeDoc.objects.all()`（不过滤 visibility）**；其他角色 `visibility='public'` + 范围条件。
- `status='indexed'` 在 **8 处**强制：`:581, :630, :657, :856, :896, :980, :1042`，以及 `:918`（`_enrich_with_doc_meta`，**唯一没有叠加权限 helper 的一处**，但它在 `retrieve()` 内被后续 `_filter_by_permission`（`:1191`）再过滤一次，现状不构成独立泄露）。
- Milvus **没有** status/metadata 字段（`utils/milvus.py:71-80` schema 只有 `id/vector_id/doc_id/chunk_index/chunk_text/page_number/seq_order/embedding`）→ **抑制只能靠 SQL 侧的 `doc_id` 过滤**，不能靠向量库。
- RAG **不**重新读 OSS，内容来自 `DocChunk.content`（`rag.py:505, 667`）或 Milvus 的 `chunk_text`。

### 19.2 设计要求（`[建议]`）

RAG 必须同时满足：

```
storage_state = promoted
AND moderation_state ∈ {approved, confirmed_unchecked}
AND status = indexed
```

**注意（本题要求）**：`is_sensitive=False` / `sensitive_checked=False` / `status='uploaded'` / `status='indexed'` / `管理员上传` / `crawler 来源` / `OSS 对象存在` / `文件能正常解析` —— **以上任一，都不等于 `moderation_state=approved`**。实现时不允许把这些作为替代判据。

**实现落点建议**：加在 `authorized_knowledge_docs()`（一处覆盖 8 个过滤点 + `retrieve_images`）。**但需注意副作用**：该方法也被 `utils/data_query.py` 之类的地方间接影响（实测 `data_query` 用的是**自己的内联范围逻辑**，不调该 helper，所以改动面是可控的）。`super_admin` 也不例外 —— **必须显式否定"super_admin 绕过审核"**，因为该方法目前对 super_admin 是 `return qs`（无任何过滤）。

---

## 20. STS（已在 §15.2(d) 展开，此处仅结论）

- 必须 `STS credentials only write quarantine`，不得写 `knowledge/`。
- 现状 `get_sts_credentials` 的生产 STS 分支**整段被注释**（`oss.py:155-168`），实际用 `sign_url('PUT')`（`:169`）—— 该签名 URL **可写任意 key**（当前实现无路径约束）。因此"只写 quarantine"必须通过**签发时就把 key 定死在 quarantine 前缀** + **回调时校验 key 前缀**双重保证，而不能只依赖 STS policy（当前根本没有生效的 policy）。
- `[建议]` 与 `UploadIntent` 一并落库，解决 ownership BLOCKED。

## 21. CALLBACK（已在 §15.2(e) 展开，此处仅结论）

- 五要素现状：metadata/size/type ✅（P1-1），hash ⚠️（分片场景跳过），**intent ❌ / ownership ❌**。
- P1-2 必须引入服务端上传意图记录，否则 callback ownership 永远是 BLOCKED。

## 22. CHUNKED_UPLOAD（已在 §15.2(f) 展开，此处仅结论）

- 不对 chunk 审核；对 merge 后的**最终对象**审核；merge 后**不得**直接 index。
- quarantine 前缀必须在 init 时确定（因为 `complete_multipart_upload` 不改变 key）。

---

## 23. CRAWLER（P1-2-P）

### 23.1 现状（`[已核实]`）

`utils/crawler.py::save_page_as_knowledge_doc`（`:372`）→ 建 doc（`:462-480`）：

```python
doc = KnowledgeDoc.objects.create(
    title=title[:512], description=title[:200],
    source_type="website", source_url=url,
    file_type="txt", file_size=len(content.encode("utf-8")),
    md5_hash=content_hash, oss_key=oss_key, oss_url=oss_url,
    visibility="public", scope="school", uploader=uploader,
    college_id=college_id, discipline_id=discipline_id,
    status="uploaded",
)
```

- OSS key：`knowledge/website/0/{YYYY-MM}/crawl_{12hex}.txt`（`:448-449`），**满足 `check_indexable` 的 `knowledge/` 前缀**。
- 触发索引：`safe_delay(index_document_task, doc.id)`（`:491`）。
- 校验：**几乎为零** —— 只有 `if not content or len(content) < 50: return None`（`:406-408`）、URL 去重（`:411-413`）、MD5 去重（`:416-419`）。**无类型白名单、无大小上限、无内容安全审核**。
- 内容来源：`NeusoftSpider`（`:214`）用 `requests` + BeautifulSoup BFS 抓 `*.neusoft.edu.cn`（`:105`），可回退 Playwright（`:237-242`）。
- 触发点：beat（`config/celery.py:36-45` 每日/每周）、`crawl_website` 管理命令、FAQ 管线（`apps/faq/tasks.py:264`）。

### 23.2 设计结论

> **crawler 也必须经过统一 moderation pipeline。不能因为"管理员配置"就默认内容安全。**

理由：爬虫抓的是**第三方网页内容**，其可控性低于内部上传（站点被篡改、被投毒、被挂马都会直达知识库并进入 RAG）。当前 `visibility="public"` + `scope="school"` + 自动索引，意味着**爬到的内容默认对全校可检索**。

`[建议]` 策略（与用户上传区分）：

| 维度 | 用户上传 | crawler |
|------|---------|---------|
| 是否走 quarantine | ✅ | ✅ |
| ClamAV | ✅ | ✅（网页文本命中概率低但成本极低） |
| rule / LLM | ✅ | ✅ |
| 是否允许用户确认 `confirmed_unchecked` | ✅（对 unavailable） | ❌ **不允许**（无"上传者"可确认；应停在 unavailable 并告警，由管理员处置） |
| 失败策略 | 提示用户 | 跳过该页 + 告警，不影响其余页 |

---

## 24. HISTORICAL_DATA（P1-2-O）

### 24.1 现状存量（`[已核实]` 结构，未查数据量）

- `KnowledgeDoc`：含 `status ∈ {uploaded,parsing,chunking,embedding,indexed,failed}` 的历史行；
- `DocChunk`：已索引文档的分块；
- Milvus：已写入的向量（`vector_id = f'doc_{doc_id}_chunk_{i}'`，`tasks.py:217`）；
- 关键：**没有任何一行有真实的审核记录**（`is_sensitive`/`sensitive_checked` 全仓库无生产写入点）。

### 24.2 方案 A：历史数据统一进入 `legacy_unchecked`

做法：migration/数据迁移为所有既有 doc 建 `ContentModeration(state='confirmed_unchecked', verdict='unavailable', policy_version='legacy', storage_state='promoted', 迁移批注)`。

- 「`legacy_unchecked`」语义：**明确表示"这是迁移前就存在、从未经过真实审核的内容"**，而不是"审核通过"。建议不要直接叫 `confirmed_unchecked`，而是新增一个**独立的、更诚实的枚举值**（或在 `confirmation_reason` 中记录 `legacy_migration`），以便未来能区分"人工确认放行"与"历史遗留"。

### 24.3 方案 B：历史数据重新审核

做法：批量把既有 doc 重新送入 pipeline（含 OSS 回调、ClamAV、LLM 等），逐份得出结论。

### 24.4 对比与推荐

| 维度 | 方案 A（legacy_unchecked） | 方案 B（重新审核） |
|------|--------------------------|------------------|
| 成本 | 极低（一条 migration + 批量 insert） | 高（N 份 × 下载 + 解析 + ClamAV + LLM；且 LLM/Vision 有费用） |
| 安全性 | 中：存量内容继续可读，但**被显式标记为未审核**，可在 UI/报表暴露 | 高：存量内容也获得真实结论 |
| 兼容性 | 高：不改变现有可读/可检索行为，**上线无 RAG 中断** | 低：审核期间若收紧门，RAG 覆盖会骤降 |
| 上线风险 | 低（可加性 migration，可回滚） | 高（长跑任务、失败重试、部分完成状态、LLM 限流） |
| 可解释性 | 好（`policy_version='legacy'` 可追溯） | 好 |

**推荐：A 打底 + B 渐进（分阶段混合）**

1. **上线阶段用 A**：migration 一次性把存量标记为 `legacy_unchecked`（`storage_state='promoted'`，保持"可读可检索"现状不被打破），保证上线当天零功能回归；
2. **上线后按批次跑 B**：后台任务对 `policy_version='legacy'` 的记录分批重新审核，结论**只能向"更严格"方向变化**（即只能把 `legacy_unchecked` 改成 `approved` 或 `rejected`，**不能**把拒绝放宽）；
3. **收口**：全部重扫完成后，`legacy_unchecked` 计数应归零；若长期不归零，作为运维告警项。

理由：单独用 A 会让"未审核内容永久可读"固化为事实标准；单独用 B 则在上线瞬间制造 RAG 覆盖缺口与长跑任务风险。混合方案在**兼容性**与**最终安全性**之间取平衡，且每一步都可独立验收。

> `[建议]` **绝对不要**把历史数据直接写成 `is_sensitive=False` 并解释为 clean —— 这正是 §八 明令禁止的等价操作。

---

## 25. FAILURE_COMPENSATION（P1-2-Q）

| # | 场景 | 最终状态 | 允许 retry | 允许 user confirmation | 允许读 | 允许 index | 需要 cleanup |
|---|------|---------|-----------|----------------------|-------|-----------|-------------|
| 1 | OSS upload 成功 / DB create 失败 | 对象存在于 quarantine，**无 DB 记录**（孤儿） | ✅ 由回收任务按前缀 + `created_at` 扫描 | — | ❌ | ❌ | ✅ **必需**（否则 quarantine 无限增长） |
| 2 | DB create 成功 / moderation 失败（异常） | `state='error'`，`storage_state='quarantine'` | ✅ 有界重试（≤3） | ❌（error 不是 unavailable） | ❌ | ❌ | 重试耗尽后保留待人工 |
| 3 | moderation approved / promotion 失败 | `storage_state='promotion_failed'`，`state='approved'` | ✅ 有界重试（≤3） | 不适用 | ❌（未 promoted） | ❌ | 重试耗尽 → `deleted` + 告警 |
| 4 | promotion 成功 / index 失败 | `promoted` + `state='approved'` + `doc.status='failed'`（现状 `tasks.py:264`） | ✅ 现有 `index_document_task` 重试机制（`max_retries=2`） | 不适用 | ✅（**已审核**） | 可重试 | 无 |
| 5 | index 成功 / RAG sync 失败 | `doc.status='indexed'` 但 Milvus 缺向量 | ✅ 重新索引（任务体已先 `delete_by_doc` + `DocChunk.delete()`，`tasks.py:210-211`，**幂等**） | 不适用 | ✅ | 可重试 | 无 |
| 6 | Celery task 重复 | 幂等（`delete_by_doc` + 重建，`tasks.py:210-241`） | — | — | ✅ | — | 无 |
| 7 | moderation task 重复 | `ModerationAttempt (moderation, attempt_seq)` 唯一约束 + CAS（§7.3/7.4） | — | — | — | — | 无 |
| 8 | **旧 moderation task 晚于新 task 完成** | `applied_seq` CAS 丢弃旧结果（§7.3） | — | — | — | — | 无 |
| 9 | 用户对 unavailable 确认两次 | 第二次因状态已非 `unavailable` 而**拒绝**（或被幂等化为 no-op） | — | 幂等 | ✅ | ✅ | 无 |
| 10 | callback 到达两次 | P1-1 已有 `existing_by_key` 幂等（`views.py:719-727`）→ 返回同一 doc；**P1-2 需确保不会把 promoted 退回 quarantine** | — | — | — | — | 无 |
| 11 | chunk merge 到达两次 | `status != 'uploading'` 直接 404（`chunked_views.py:287`），事务 + 行锁（`:281`） | — | — | — | — | `complete_multipart_upload` 只执行一次 |

**跨场景共性要求**：

- 场景 1 的孤儿回收是**当前完全缺失**的能力，且 P1-2 会把写 OSS 与建 DB 的时序拉开（先 quarantine 后审核），孤儿窗口变大，**必须实现**。
- 所有"重试耗尽"的终态必须**告警**，不能静默。
- 所有 cleanup 必须**幂等**（重复清理不报错）。

---

## 26. API_DESIGN（P1-2-R）

### 26.1 确认接口（只设计，不实现）

```
POST /api/knowledge/docs/{id}/moderation-confirm/
```

| 项 | 设计 |
|----|------|
| 前置条件 | `state == 'unavailable'`；**其他状态下必须拒绝**（HTTP 409 或 400） |
| **绝对不能绕过** | `virus` / `harmful` / `negative_guidance` / `sensitive`（即 `state='rejected'` 一律拒绝）及 `mixed` |
| 请求体 | `{ "reason": "<必填，非空>" }` |
| 权限 | 上传者本人；或具备审核权限的管理员（建议独立权限类，复用 `config/permissions.py` 的风格） |
| 副作用 | 写 `state='confirmed_unchecked'`、**保留 `verdict='unavailable'`**（§6.1）、`confirmed_by`/`confirmed_at`/`confirmation_reason`；写审计日志（可复用 `admin_panel.OperationLog` 的形态，`apps/admin_panel/models.py:69`） |
| 幂等 | 第二次调用：状态已非 `unavailable` → 返回当前状态 + `already_confirmed`，不报 500 |
| 是否可撤销 | `[建议]` 不提供撤销接口；如需撤销，走管理员"重新审核" |

```
GET /api/knowledge/docs/{id}/moderation/
```

返回当前审核态（**不泄露 findings 中的 excerpt 给普通用户**）：

```
state, verdict, categories, message, requires_confirmation,
checked_at, policy_version, storage_state,
can_read, can_download, can_preview, can_index
```

`[建议]`：`findings` 仅对审核管理员返回，且经过 §8.2 脱敏。

### 26.2 统一上传响应（提案）

```
{
  "id": 123,
  "upload_state": "accepted",           // 新增：受理态（区别于入库完成）
  "storage_state": "quarantine",
  "moderation_state": "pending",
  "moderation_verdict": null,
  "moderation_categories": [],
  "moderation_findings": [],            // 普通用户恒为空
  "moderation_message": "已提交安全审核",
  "requires_confirmation": false,
  "can_read": false,
  "can_download": false,
  "can_preview": false,
  "can_index": false,
  "status": "uploaded"                  // 保持现有字段，语义不变（索引状态）
}
```

**兼容性要求（关键）**：
- `status` 字段**必须保留**且语义不变（`indexed` 仍表示索引完成），因为前端与 `KnowledgeDocFilter`（`views.py:64`）、`data_query`、RAG 门都在用。
- 新增字段是**加性的**，前端可忽略。
- **行为变更必须在 `docs/前端对接接口文档-*.md` 中交接**（团队约定：前端代码不由本人修改，见既有规范）。`[建议]` 明确告知前端：**上传成功后 `can_read` 可能为 false**，这是 P1-2 的**破坏性行为变更**，需要前端配合（例如显示"审核中"）。这是 P1-2 最大的产品面变化，必须提前沟通。

---

## 27. TEST_MATRIX（P1-2-S）

`[建议]` 矩阵（**不写测试代码**，仅列用例；沿用 P1-1 已建立的测试基建：`apps/knowledge/tests.py`、mock OSS、`settings_test` 内存 SQLite）：

| # | 用例 | 输入 | 预期 state | 预期 verdict | promote | index | 可读 | 可下载 | 进 RAG |
|---|------|------|-----------|-------------|---------|-------|------|--------|--------|
| 1 | clean | 正常 PDF + 全部检测器 clean | `approved` | `clean` | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2 | virus | EICAR 样本 | `rejected` | `virus` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 3 | rule hit | 含涉密关键词文本 | `rejected` | `sensitive` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 4 | LLM hit | 危险指导类文本 | `rejected` | `harmful`/`negative_guidance` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 5 | Vision hit | 违规图片 | `rejected` | `harmful`/`sensitive` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 6 | mixed | rule=sensitive + LLM=harmful | `rejected` | `mixed` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 7 | timeout | 检测器超时 | `unavailable` | `unavailable` | ❌ | ❌ | 仅上传者 | ❌ | ❌ |
| 8 | service unavailable | ClamAV 连接拒绝 | `unavailable` | `unavailable` | ❌ | ❌ | 仅上传者 | ❌ | ❌ |
| 9 | parser failure | 损坏的 PDF | `error` | `error` | ❌ | ❌ | ❌ | ❌ | ❌ |
| 10 | empty extraction | 0 字节 / 无文本层扫描件 | `unavailable` | `unavailable` | ❌ | ❌ | 仅上传者 | ❌ | ❌ |
| 11 | duplicate moderation | 同 doc 提交两次 | 幂等，单条 attempt | — | 一次 | 一次 | — | — | — |
| 12 | stale task | 旧 attempt 晚到 | `applied_seq` 拒绝覆盖，保持新结论 | 新结论 | — | — | — | — | — |
| 13 | promotion failure | `copy_object` 抛错 | `promotion_failed` | `clean`（保留） | ❌（重试） | ❌ | ❌ | ❌ | ❌ |
| 14 | callback forgery | 伪造 oss_key / 非本范围 | 400 | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 15 | wrong size | 声明 size ≠ OSS 实测 | 400 | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 16 | wrong type | Content-Type 与后缀不符 | 400 | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 17 | wrong md5 | ETag ≠ 声明 md5 | 400 | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 18 | **wrong ownership** | 认领他人 intent 的 oss_key | 400 | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 19 | duplicate callback | 同 oss_key 回调两次 | 幂等返回同 doc；**不退回 quarantine** | — | — | — | — | — | — |
| 20 | chunk merge concurrency | 并发 merge 同一 session | 一次成功，另一次 404 | — | — | — | — | — | — |
| 21 | large file | 400MB 视频 | 流式处理，无 OOM；超预算 → `unavailable` | — | — | — | — | — | — |
| 22 | archive bomb | 解压膨胀 1:1000 | 拒绝 / `error`，不得 OOM | — | — | — | — | — | — |
| 23 | macro/script | 含宏 doc/xlsm | 不执行宏；解析在受限进程内 | — | — | — | — | — | — |
| 24 | **quarantine access** | 直接请求 quarantine 对象的签名 URL | 不返回签名 URL / URL 无权限 | — | — | — | ❌ | ❌ | ❌ |
| 25 | **signed URL suppression** | 审核中 doc 走 detail/download/preview | 三处均不产出可用 URL | — | — | — | ❌ | ❌ | ❌ |
| 26 | **RAG suppression** | 审核中 doc 走 `retrieve()` | 检索不到 | — | — | — | — | — | ❌ |
| 27 | **Milvus suppression** | 审核中 doc 的向量已存在 | 因 SQL 侧 `doc_id` 过滤而不被命中 | — | — | — | — | — | ❌ |
| 28 | confirmation | 对 `unavailable` 调确认接口 | `confirmed_unchecked` | `unavailable`（保留） | ✅ | ✅ | ✅ | ✅ | ✅ |
| 29 | confirmation 绕过尝试 | 对 `rejected/virus` 调确认接口 | 拒绝（409/400） | — | ❌ | ❌ | ❌ | ❌ | ❌ |
| 30 | 历史数据 | `legacy_unchecked` 存量 | 保持可读（方案 A） | `unavailable` | 已 promoted | 保持 | ✅ | ✅ | ✅ |

**测试基建要点（沿用 P1-1 已知坑，见 `apps/knowledge/tests.py`）**：
- patch 目标是**使用处**（`apps.knowledge.chunked_views.safe_delay`），不是定义处；
- `select_for_update()` 在 SQLite 无操作 → #20 只能验证"重复请求被拒"，真实串行化需 MySQL 环境；
- 审核测试必须**全 mock** OSS / ClamAV / LLM / Vision，禁止任何外网请求（P1-1 曾发生真实访问生产 OSS 的事故，当前测试以 `aliyuncs` 命中 0 为验收基线）。

---

## 28. MIGRATION_IMPACT（P1-2-T）

### 28.1 现状（`[已核实]`）

- `knowledge` app 迁移链：`0001_initial` → `0002_add_visibility` → `0003_...` → `0004_...` → `0005_...` → `0006_chunkeduploadsession_and_more` → `0007_knowledgedoc_index_diagnostics`。**最新为 0007**。
- `0007` 是 `AddField` 纯加性迁移（`index_error`/`index_attempts`/`index_started_at`/`failed_at`），**无数据迁移** —— 这是 P1-2 应沿用的范式。
- 相关 FK / 约束：
  - `KnowledgeDoc.discipline/category/college/department` → `SET_NULL`；`uploader` → `SET_NULL`；
  - `DocChunk (doc, chunk_index)` 唯一约束 `uk_chunk_doc_index`（`models.py:215-220`）；
  - `ChunkedUploadSession` 主键为 `upload_id`（CharField），索引 `idx_cus_user_status`、`idx_cus_md5_status`；
  - `Keyword.doc` CASCADE；`UploadLog.knowledge_doc` SET_NULL。

### 28.2 P1-2 迁移影响（`[建议]`）

| 项 | 内容 |
|----|------|
| **新模型** | `ContentModeration`（OneToOne → KnowledgeDoc, CASCADE）、`ModerationAttempt`（FK → ContentModeration, CASCADE）；建议同时新增 `UploadIntent`（若采纳 §15.2(e)） |
| **新字段** | `KnowledgeDoc` 上建议只加**只读派生字段**（如 `moderation_state_cached`）用于列表筛选；`index` 加在 `(storage_state)` 与 `(state, storage_state)` 组合上 |
| **索引** | `ContentModeration(state, storage_state)`、`ContentModeration(doc_id)`（OneToOne 自带唯一索引）、`ModerationAttempt(moderation, attempt_seq)` 唯一、`UploadIntent(oss_key)` 唯一 + `(user, used_at)` |
| **数据迁移** | 方案 A 的批量回填（§24.4）—— **必须分批**（`bulk_create(batch_size=...)` 或分页 `.iterator()`），避免一次性锁表；**必须幂等**（`get_or_create`） |
| **回滚** | ① 新表可整表 drop，不影响 `KnowledgeDoc`；② 若在 `KnowledgeDoc` 加了派生字段，回滚需 `RemoveField`（有数据丢失但不影响主流程）；③ **migration 必须可加性**：新字段全部 `null=True` 或带 `default`，绝不 `null=False` 无默认值（否则老代码在迁移后写入会失败） |
| **部署顺序** | **先加表/字段 → 再发代码 → 再回填 → 最后收紧门**。因为 CI 会在 push 到 main 时自动 `migrate`（§2.5），**代码与迁移的发布是同一时刻**，所以迁移必须对"迁移已执行但新代码尚未接管"的中间态安全。 |
| **本轮** | **绝对不创建 migration**（§一）。以上均为设计 |

**与旧代码的二进制兼容性**：P1-2 上线时，旧代码（无审核概念）与新表并存。旧代码的 `_admin_scope_filter` 不会过滤审核状态 —— 因此**在代码部署完成前，"门"是不生效的**。这段时间必须极短，且建议用一个开关（settings 里的 `MODERATION_ENFORCE`）分两步：先只写不判（观察数据），再开启判定。这样可避免"迁移完成但检测器还没跑，全库瞬间不可读"。

---

## 29. DEPENDENCIES（P1-2-U）

### 29.1 现状（`[已核实]`）

`backend/requirements.txt` **只有 20 行**，声明了：Django、DRF、simplejwt、cors-headers、django-filter、mysqlclient、drf-spectacular、decouple、faster-whisper、modelscope、librosa、soundfile、pydub、beautifulsoup4、lxml、numpy、chromadb、celery、redis、imageio-ffmpeg、Pillow。

**实际被 import 但未声明的（关键缺口）**：

| 类别 | 缺失的包 | 后果 |
|------|---------|------|
| OSS | **`oss2`** | 核心功能无法运行 |
| HTTP | **`requests`** | 全部外部调用无法运行 |
| 解析 | `PyMuPDF`(fitz)、`pdfplumber`、`python-docx`、`python-pptx`、`openpyxl`、`xlrd`、`docx2txt` | 文档提取全链路 |
| 向量 | `pymilvus`、`sentence_transformers`、`torch` | Milvus / 本地 embedding |
| 分词 | `jieba` | RAG 查询词提取 |
| 其它 | `httpx`、`playwright`、`pymysql`、`baidusearch`、`PIL`(Pillow 已声明) | 分散 |

→ **P1-2 之前必须先解决依赖声明的卫生问题**，否则新增 ClamAV 客户端/解析依赖会延续"靠手工在生产 venv 里装"的隐性契约：CI 的 `pip install -r requirements.txt` **无法重建可用环境**，任何依赖变更都是不可复现的。

### 29.2 P1-2 新增依赖分类（`[建议]`）

| 依赖 | 分类 | 备注 |
|------|------|------|
| ClamAV 客户端 | **Python dependency**（可选） | 建议用 `clamd` 包，或直接 `socket`（无新依赖，推荐后者以缩小供应链面） |
| ClamAV 引擎 | **system dependency**（必需） | `clamav-daemon` + `freshclam`；常驻内存约 1–2GB，需评估服务器规格 |
| `python-magic` / 文件头识别 | **Python dependency**（建议） | 当前**完全按扩展名判断类型**（`normalize_file_type`、`get_file_ext`、`ALLOWED_FILE_TYPES`），可被改名绕过（如 `.exe` 改名 `.pdf`）。P1-2 的审核若仍只看扩展名，则"类型校验"形同虚设。**强烈建议引入魔数嗅探作为审核输入之一** |
| `antiword` | **system dependency** | 已在用（`extractor.py:182`），**未在部署脚本安装** |
| LibreOffice (`soffice`) | **system dependency** | 已在用（`extractor.py:208`），**未在部署脚本安装**；体积大（数百 MB） |
| `ffmpeg` | **system dependency** | 已在用（`start.bat` 里加了 `C:\wwwroot\ffmpeg` 到 PATH） |
| LLM / Vision 客户端 | **external service** | 已在用（`llm_client.chat` / `chat_vision`） |
| Celery / Redis | **已在 requirements**，但**部署无 worker 单元**（§2.4） | — |

---

## 30. DEPLOYMENT（P1-2-U / W4）

### 30.1 现状（`[已核实]`）

- 无 Dockerfile / docker-compose；`.github/workflows/deploy.yml` + `deploy/nginx.conf` + `start.bat`（Windows 本地 `runserver`）。
- 部署流程：`git pull` → `pip install -r requirements.txt` → `manage.py migrate` → `systemctl restart campus-backend` → `curl /api/docs/`。
- **没有 celery worker / beat 的单元或重启步骤**（§2.4）。
- nginx：`client_max_body_size 500m` 仅作用于 `/api/knowledge/upload/`；`/api/chat/` 关闭缓冲（SSE）。

### 30.2 P1-2 部署要求（`[建议]`）

1. **新增 celery worker 与 beat 的托管单元**（systemd），并在部署脚本中重启 —— 否则 P1-1 的 `W4` 与 P1-2 的审核任务都会长时间跑旧代码。
2. **审核 worker 与 Web worker 分离**（建议独立队列，如 `moderation` 队列）：审核任务时长（ClamAV + LLM + Vision）远大于普通索引，混在同一队列会让上传链路排队。
3. **migration 与代码的发布协调**（§28.2）：用 `MODERATION_ENFORCE` 开关分两步发布。
4. **健康检查需加强**：当前 `/api/docs/` 只证明进程活着，不证明审核链路可用。建议新增一个内部自检端点（报告 ClamAV 可达性 / 队列积压 / `legacy_unchecked` 计数）。
5. **系统依赖安装**：`antiword`、`libreoffice`（或 `--headless` 最小安装）、`ffmpeg`、`clamav` 必须进入部署脚本，且**在 requirements 重建环境之前**。
6. **磁盘**：large 档文件的临时文件（`extract_text_from_oss` 落盘，`extractor.py:271`）需要磁盘配额与清理策略；`tempfile.mkstemp` 在 `finally` 中删除（`:289-293`），但进程被 kill 时会残留。

---

## 31. FINAL_ARCHITECTURE（P1-2-V）

按当前代码实际情况**修正**后的架构图（改动点已标注）：

```
                         Upload (E1..E8)
                               |
                               |  STS/init 时落库 UploadIntent ← 🆕 解决 ownership BLOCKED
                               v
                    ┌──────────────────────┐
                    │  Quarantine  OSS     │   🆕 新顶层前缀
                    │  quarantine/{scope}/ │   （现为 knowledge/…，需迁移）
                    │        {owner}/…     │
                    └──────────┬───────────┘
                               |
                               v
                    ┌──────────────────────┐
                    │ KnowledgeDoc         │
                    │  + ContentModeration │   🆕 1:1 当前态
                    │  + ModerationAttempt │   🆕 1:N 历史
                    │  state=pending       │
                    └──────────┬───────────┘
                               |
                               v
                     Content Extraction        ← 修：unsupported/empty/dep_missing → unavailable
                     extract_content()             （现：未知类型当文本读 → 乱码入库）
                               |
        +----------------------+----------------------+
        |                      |                      |
        v                      v                      v
     ClamAV                  Rule                 LLM / Vision
   （原始字节，            （sensitive_filter     （llm_client.chat /
     先于解析器 🆕）          + 定位 + 脱敏）        chat_vision，并行不短路）
        |                      |                      |
        +----------------------+----------------------+
                               |
                               v
                     Result Aggregator
                  （拒绝级 > unavailable/error > clean）
                               |
             +-----------------+------------------+
             |                                    |
             v                                    v
        REJECTED                              APPROVED
   state=rejected, verdict=virus/           state=approved, verdict=clean
   harmful/negative_guidance/sensitive/            |
   mixed                                     OSS copy → knowledge/…
   ❌ 不 promote  ❌ 不 index  ❌ 不可读          storage_state=promoted 🆕
   （unavailable/error 分支：                      |
     仅上传者可见，可由用户显式确认                  v
     → confirmed_unchecked，绝不由超时产生）    KnowledgeDoc（正式态）
                                                   |
                                                   v
                                          index_document_task
                                     （任务体内复核 storage+moderation 🆕）
                                                   |
                                                   v
                                          DocChunk  +  Milvus
                                                   |
                                                   v
                                                  RAG
                              （门：promoted AND state∈{approved,
                                confirmed_unchecked} AND status=indexed）

  旁路（必须同样收敛，当前未收敛 🆕）：
    crawler ──────────► 同一 pipeline（不允许因"管理员配置"默认安全）
    apps/faq/tasks.py ─► 加 readable_docs 门（当前直接读 OSS 原文喂 LLM）
    scripts/seed_test_docs.py ─► 当前完全绕过，必须改为调用任务
    text upload (E3) ──► 同一 pipeline
```

---

## 32. IMPLEMENTATION_PHASES（P1-2-W）

按**实际审计结果**重新拆分，每阶段**可独立验收**、可独立回滚：

| 阶段 | 内容 | 验收标准 | 前置 |
|------|------|---------|------|
| **P1-2A 部署与迁移前置** | ① 补齐 `requirements.txt`（§29.1）；② 新增 celery worker/beat 的 systemd 单元并在 CI 重启；③ 引入 `MODERATION_ENFORCE` 开关（默认关）；④ 确认 migration 只加性 | 在干净 venv 中 `pip install -r requirements.txt` 可跑通全部测试；部署脚本含 worker 重启；开关关闭时行为与现状**完全一致** | 无 |
| **P1-2B OSS 原语** | `iter_object_chunks` / `download_to_file`（流式、限字节）+ `copy_object`；单测覆盖 | 能流式读取 500MB 对象而内存不随文件增长；`copy_object` 后 `head_object` 一致 | A |
| **P1-2C 数据模型** | `ContentModeration` / `ModerationAttempt`（+ 可选 `UploadIntent`）；migration（纯加性）；无任何行为变更 | `migrate` 成功且可回滚；旧代码在迁移后仍能正常读写；`python manage.py test apps.knowledge` 全绿 | A |
| **P1-2D 状态机与聚合器**（纯函数） | 状态机 + CAS 应用逻辑 + 聚合优先级。**不接任何检测器、不动上传链路** | 单测覆盖 §27 中 #6/#7/#8/#11/#12/#13/#28/#29 的**纯逻辑**部分 | C |
| **P1-2E 内容提取重构** | `extract_content()` 契约（含 `unsupported/empty/dependency_missing/failed`）；修正"未知类型当文本读"；文本长度上限 | 单测：损坏 PDF / 未知扩展名 / 缺依赖 各自得到**非 clean**结论 | C |
| **P1-2F 检测器接入** | ClamAV（复用 `CircuitBreaker`）+ Rule（包装 `check_sensitive` + 定位 + 脱敏）+ LLM + Vision，均带超时/重试/不可用判定 | 每个检测器在"不可用"时产出 `unavailable` 而**非** clean；EICAR 端到端用例通过 | B, D, E |
| **P1-2G 上传流水线 + quarantine 迁移** | `upload_pipeline.py`；E1–E8 全部改走 quarantine；STS/callback/chunk 的 intent 与前缀校验；**promotion** | 全链路：上传 → 审核 → promote → index；未通过的文档**无法**通过 detail/download/preview 拿到 URL | F |
| **P1-2H 读路径与索引门收紧** | §17.2 全表加门（含 `data_query`、`sse.py`、`rag.py`）；`check_indexable` 升级为读写双检；修 `seed_test_docs` bypass | 审核中/拒绝的文档：REST 三出口、RAG、Milvus 检索、FAQ 管线**全部**取不到 | G |
| **P1-2I 旁路收敛** | crawler 走 pipeline（不允许用户确认）；`apps/faq/tasks.py` 加门；`scripts/*` 收敛 | crawler 产出的文档在审核通过前不进 RAG；FAQ 不再消费未审核文档 | H |
| **P1-2J 历史数据** | 方案 A 回填（`legacy_unchecked`）+ 分批 B 重扫任务 + 收口告警 | 上线零功能回归；重扫只向更严格方向变化；`legacy_unchecked` 计数可见 | H |
| **P1-2K 确认 API 与前端交接** | §26 两个接口；`docs/前端对接接口文档-*.md` | 确认接口对 `rejected` 一律拒绝；前端已知悉 `can_read=false` 的语义 | H |
| **P1-2L 测试矩阵与压测** | §27 全部 30 个用例；大文件/归档炸弹/并发 | 全绿且**零外网请求**（`aliyuncs` 命中 0） | K |
| **P1-2M 上线** | 打开 `MODERATION_ENFORCE`；灰度；监控 | 审核队列积压、`unavailable` 比例、`legacy_unchecked` 计数在可接受范围 | L |

**顺序理由**：A→B→C 是纯地基（零行为变更，风险最低，可先落地观察）；D→E→F 是能力建设；G 是真正的行为切换点（**必须最后开关控制**）；H/I 是收紧；J 处理存量；L/M 收口。**G 之前上线都不改变现有行为**，这是本拆分最重要的安全边界。

---

## 33. 三项 `[存疑/待人工确认]`（未做猜测性断言）

1. **promotion 的中间态**（§3.2）：`copy + delete` 存在短暂双份对象。是否可接受需业务确认；若不可接受需改为两段式 + 回收任务。
2. **上传时已把内容发给外部 LLM**（§12）：`_ai_classify` 在**审核之前**就把文档内容/图片发给 LLM 生成元数据（`views.py:1418,1857-1878`）。P1-2 要明确这是否可接受，以及是否需要把"先审核再送外部模型"作为要求。**本轮只记录，不修。**
3. **`llm_client` 的 mock 降级条件**（§12）：`_mock_chat` / `_mock_embed` 的触发条件未逐一核实，审核策略**不得**依赖"未核实的行为"。实现阶段需先确认。

---

## 34. BLOCKERS

按"是否必须先解决"排序：

| # | Blocker | 为什么阻塞 | 归属阶段 |
|---|---------|-----------|---------|
| **B1** | **CI 在 main 上自动 migrate + 无 worker 重启**（§2.4/§2.5） | 任何 migration 会自动上生产且不可控；worker 跑旧代码 | P1-2A |
| **B2** | **`requirements.txt` 无法重建可用环境**（§29.1） | 依赖不可复现时，新增审核依赖与"环境一致"无法保证 | P1-2A |
| **B3** | **OSS 无服务端读内容 / 无 copy 原语**（§3.4） | quarantine 方案的 promote 与 ClamAV 流式扫描都做不了 | P1-2B |
| **B4** | **`scripts/seed_test_docs.py` 完全绕过索引入口**（§2.1） | 闸门建好后仍存在写入 DocChunk/Milvus 的旁路 | P1-2H |
| **B5** | **callback ownership BLOCKED**（§15.2(e)） | 无法证明对象归属；与 moderation 主体风险独立 | P1-2G（建议独立提交） |

> 说明：B1–B3 属于"进入正式实施前必须闭合"；B4/B5 可以在对应阶段内闭合，但如果不在计划内就会被遗忘 —— 因此在此显式登记。

## 35. RISKS

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | **收紧读门导致 RAG 覆盖骤降** | 用户体验断崖 | `MODERATION_ENFORCE` 开关 + 方案 A 存量打底（§24.4） |
| R2 | **检测器误报把正常资料挡在门外** | 上传者反复重传、管理员负担 | `unavailable` 允许显式确认；`rejected` 提供申诉/重新审核通道；`sensitive` 规则误报率最高，建议审核期先"只记录不阻断"观察一段时间 |
| R3 | **审核链路耗时远超索引**（ClamAV + LLM + Vision） | 上传到可读的延迟从秒级到分钟级 | 独立队列；先做快速检测（ClamAV + rule）给出初步结论，LLM/Vision 异步补齐；前端明示"审核中" |
| R4 | **成本**：LLM/Vision 对每一份文档都要调用 | 费用与限流 | 分档：小文件全检，大文件截断；Vision 仅对有图片的文档；规则先做粗筛（rule 命中高危可直接 rejected，跳过 LLM）*（注意：这与 §13.1 "不短路"冲突，需在 D 阶段明确取舍）* |
| R5 | **历史数据迁移锁表** | 生产不可用 | 分批 + 幂等 + 低峰执行（§28.2） |
| R6 | **ClamAV 常驻内存 1–2GB** | 服务器资源紧张 | 上线前评估；必要时用远程扫描服务 |
| R7 | **`super_admin` 绕过**（§19.2） | 审核对最高权限角色失效 | 门的实现中**显式**否定 super_admin 例外，并在测试矩阵加用例 |
| R8 | **扩展名可被改名绕过**（§29.2） | 类型校验与针对性策略失效 | 引入魔数嗅探 |
| R9 | **前端未同步**（`can_read=false`） | 前端把"审核中"当"上传失败" | P1-2K 提前交接接口文档（团队约定：前端由专人负责） |
| R10 | **归档炸弹/宏/解析器漏洞** | 服务端被 DoS 或 RCE | §16.3 限额 + 隔离进程 + 超时 + 元数据/长度边界测试 |

---

## 36. P1-2_READINESS

**READY**

理由：本轮审计未发现**架构性**阻塞 —— P1-1 已把闸门放在正确位置（任务体内），生效路径唯一且可扩展；设计所需的原语中，超时/重试/熔断（`utils/http_client.py`）、规则引擎（`utils/sensitive_filter.py`）、LLM 与 Vision（`utils/llm.py`）、OSS 元数据（P1-1 的 `head_object`）**均已存在**；缺的只有 OSS 服务端读/复制两个薄原语（`oss2` 已具备底层能力）。

但**不等于可以立刻开写**：B1–B3 必须先闭合（P1-2A/A/B），否则 migration 会不可控地上生产、环境不可复现、quarantine 无法落地。

**RECOMMENDATION**

1. **先做 P1-2A**（部署与迁移前置），它是一个**零行为变更**的阶段，可以立即开始并独立验收；它同时消掉 B1/B2，并让 `W4` 从"提醒"变成"流程"。
2. **再做 P1-2B/C/D**（原语、模型、纯逻辑状态机与聚合器），这三个阶段**完全不改变现有上传/读取行为**，风险最低，却已能覆盖 §27 中大量纯逻辑用例。
3. **行为切换集中在 P1-2G**，用 `MODERATION_ENFORCE` 控制，并且**存量先按方案 A 打底**（§24.4），保证上线当天零功能回归。
4. **B5（callback ownership）建议独立成一个提交**，不要和 moderation 主体混在一起 —— 两者的失败模式与回滚方式完全不同。
5. 最后，把 §八 的判断原则写进 P1-2 的验收清单：**`is_sensitive=False` / `sensitive_checked=False` / `status=uploaded` / `status=indexed` / 管理员上传 / crawler 来源 / OSS 对象存在 / 文件能正常解析 —— 以上任一都不等于 `moderation_state=approved`。**

========================================
Phase 1-2 Design Audit — END
========================================

---

*本轮为只读审计。未修改任何项目文件，未创建 migration，未连接任何生产服务，未 commit / push。本报告位于仓库根目录（与三份既有审计报告同处），untracked，不进入版本库。*
