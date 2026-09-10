# P1-2B Knowledge Consumers & Bypass Audit

> **阶段**：P1-2B（知识消费者与绕过路径只读审计）
> **报告时间**：2026-09-10
> **报告位置**：`E:\github\campus_resource\P1-2B_Knowledge_Consumer_Audit_2026-09-10.md`（仓库根目录，**untracked，不得提交**）
> **工作目录**：`e:\github\campus_resource\backend`
> **本阶段性质**：**只读审计**。未修改任何生产代码，未执行任何迁移，未写入任何数据库 / OSS / Milvus。
> **一句话结论**：P1-2A 建好了审核状态的"容器"，但**全仓库没有任何一个消费者读它**。唯一的门 `check_indexable()` 至今只检查 OSS Key 前缀。

---

## 1. Executive Summary

P1-2A 交付了 `ContentModeration` / `ModerationAttempt` / 状态常量 / `quarantine` OSS 原语。本轮审计的核心发现是：

**`ContentModeration` 与 `ModerationAttempt` 在整个业务流程中被引用的次数为 0。**

全仓库对这两个模型的引用只有四处：模型定义本身、migration 0008、`moderation.py` 的状态常量、以及 P1-2A 自己新增的测试。**没有任何视图、任务、脚本、RAG、FAQ、爬虫读取过 `moderation_state` 或 `storage_state`。**

唯一的集中式门 `check_indexable()` 被调用的地方**只有一处** —— [tasks.py:147](backend/apps/knowledge/tasks.py#L147) —— 而它的实现**只检查 `oss_key` 是否以 `knowledge/` 开头**。审核状态、结论、存储状态、`ContentModeration` 是否存在，**一律不检查**。

由此得到三层结论：

1. **索引门形同虚设**：`CURRENT INDEX GATE: FAIL`。任何 `moderation_state`（含 `rejected`、`unavailable`）与任何 `storage_state`（含 `quarantine`）都能通过。
2. **最大绕过点不在上传链路，而在脚本**：`scripts/seed_test_docs.py` 完整复制了"分块 → Embedding → 写 Milvus → 写 DocChunk"的全套流程，**完全不经过 `index_document_task`**，因此也不经过 `check_indexable`。它甚至**必须**绕过——因为它写入的 `oss_key` 是 `seed/…` 前缀，走那条门只会被拒。
3. **最大的 LLM 消费绕过在上传主链路上，而且是自动的**：`extract_freshness_task` 在**每一条上传路径**上被触发，它调用 `extract_freshness_for_doc()`，该函数只做 `KnowledgeDoc.objects.get(id=doc_id)`，然后**下载 OSS 全文并送给 LLM**。全程无任何状态检查。文档在 `moderation_state=pending` 时就已经进入了 LLM。

一个必须点明的结构性事实：**当前 RAG 的实际门禁是 `KnowledgeDoc.status == 'indexed'`**。这不是审核门，它只表示"已经完成过索引"。整个系统的安全语义目前挂在一个纯粹描述"处理进度"的字段上。

---

## 2. Git Baseline

```
git status --short --branch
## phase-1-1-security-stopgap

git rev-parse HEAD
174a64889643a09ebbb4e23d605ae4b8f131ac24

git rev-parse origin/main
001823baa7d3c25bf1deb985432c172c0f3a055f
```

| 项 | 期望值（任务书） | 实际值 | 结论 |
|----|------------------|--------|------|
| 分支 | `phase-1-1-security-stopgap` | `phase-1-1-security-stopgap` | ✅ 一致 |
| HEAD | `174a64889643a09ebbb4e23d605ae4b8f131ac24` | `174a64889643a09ebbb4e23d605ae4b8f131ac24` | ✅ 一致 |
| origin/main | `8c2d0e3e7fb4fad87e4ffb2b45055c880c8e91df` | `001823baa7d3c25bf1deb985432c172c0f3a055f` | ⚠️ **不一致** |

**⚠️ 必须披露的基线偏差：`origin/main` 已前进。** 任务书给出的 `8c2d0e3e…` 是 P1-2A 报告时的取值；当前远端 `main` 已经是 `001823baa7d3c25bf1deb985432c172c0f3a055f`（即有人在此期间向 `main` 推送了新提交）。本地 HEAD 仍是 `174a6488…`，本地分支未变。**本阶段未 fetch、未 pull、未 checkout、未 reset、未 stash**，仅执行了 `rev-parse` 读取远端引用。这不影响本次审计的任何结论（审计对象是本地工作树），但必须记录：**远端 `main` 与本地基线已经分叉，未来合并前需要重新评估。**

工作树状态（与 P1-2A 结束时**完全一致**，本轮审计未产生任何改动）：

```
 M backend/apps/knowledge/models.py
 M backend/apps/knowledge/moderation.py
 M backend/apps/knowledge/tests.py
 M backend/utils/oss.py
 M code-changes/2026-09-10.md
?? backend/apps/knowledge/migrations/0008_contentmoderation_moderationattempt_and_more.py
?? database-changes/content_moderation.md
?? database-changes/moderation_attempt.md
?? security_moderation_phase0_audit.md
?? security_moderation_phase1_0_environment.md
?? security_moderation_phase1_1_stopgap.md
?? security_moderation_phase1_2_design_audit.md
?? security_moderation_phase1_2a_implementation.md
```

---

## 3. P1-2A Current Foundation

已建立且**确认存在**的基础设施（本轮只读，未改动）：

| 设施 | 位置 | 实际状态 |
|------|------|----------|
| `ContentModeration` | [models.py:281](backend/apps/knowledge/models.py#L281) | 模型存在；**全仓库无业务读写方** |
| `ModerationAttempt` | [models.py:443](backend/apps/knowledge/models.py#L443) | 模型存在；**全仓库无业务读写方** |
| `state` / `verdict` / `storage_state` | [moderation.py:51-103](backend/apps/knowledge/moderation.py#L51) | 常量与 choices 存在 |
| `INDEXABLE_MODERATION_STATES` | [moderation.py:110](backend/apps/knowledge/moderation.py#L110) | `frozenset({approved, confirmed_unchecked})` |
| `applied_seq` | models.py | 字段存在，仅测试使用；**无任何 CAS 调用方** |
| `quarantine_key` / `promoted_key` | models.py | 字段存在；**无任何写入方** |
| `quarantine/` 命名空间原语 | [oss.py:72-108](backend/utils/oss.py#L72) | 原语存在；**无任何业务调用方** |
| `build_quarantine_key` | [oss.py:84](backend/utils/oss.py#L84) | **仅被测试调用** |
| `copy_object` / `iter_object_chunks` / `promote_object` | oss.py | **仅被测试调用** |

**关键验证**：`grep -rn "ContentModeration\|ModerationAttempt\|is_indexable_moderation_state\|INDEXABLE_MODERATION_STATES"` 在 `apps/`、`utils/`、`scripts/`、`config/` 中（排除模型自身、migration、P1-2A 测试）**命中数为 0**。

`check_indexable` 的全部调用点：

```
apps/knowledge/tasks.py:133   from apps.knowledge.moderation import check_indexable
apps/knowledge/tasks.py:147   allowed, reason = check_indexable(doc)
config/celery.py:67           （仅注释提及）
```

**`quarantine/` 前缀在全仓库出现的位置**：只有 P1-2A 新增的常量、模型字段、migration、测试。**没有任何代码向 `quarantine/` 写入，也没有任何代码从 `quarantine/` 读取。** 该命名空间目前是零使用的。

---

## 4. KnowledgeDoc Data Flow

### 4.1 主数据流

```
                          ┌─────────────────────────────────────┐
                          │          上传入口（写 side）          │
                          └─────────────────────────────────────┘
   FileUploadView:1240 ─┐
   BatchFileUploadView:1517 ─┤
   TextUploadView:910 ─┤        ┌──────────────────────────┐
   UploadSTSView:615 ─┼───────►│  OSS 对象                 │
   UploadCallbackView:675 ─┤     │  knowledge/{scope}/{id}/… │
   chunked upload        ─┤     └────────────┬─────────────┘
   (chunked_views.py)    ─┘                  │
                                             │ oss_key
                                             ▼
                                    ┌──────────────────┐
                                    │   KnowledgeDoc   │  status='uploaded'
                                    │  (MySQL 元数据)    │  visibility / college / dept
                                    └────────┬─────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
              ▼                              ▼                              ▼
   ┌────────────────────┐      ┌──────────────────────────┐    ┌────────────────────┐
   │ extract_freshness  │      │  index_document_task     │    │  直接消费者（无 task）│
   │ _task（自动）        │      │  tasks.py:111            │    │                      │
   │ tasks.py:36         │      │                          │    │  FAQ tasks.py:242    │
   │   ↓                 │      │  ① check_indexable()★    │    │  seed_test_docs.py   │
   │ extract_freshness   │      │  ② extract_text_from_oss │    │   :857               │
   │ _for_doc extractor  │      │  ③ chunk_document        │    │                      │
   │   :414              │      │  ④ llm_client.embed      │    │  绕过 task 全部环节    │
   │   ↓                 │      │  ⑤ milvus.insert_chunks  │    │                      │
   │ 下载 OSS 全文 → LLM  │      │  ⑥ DocChunk.bulk_create  │    └──────────┬───────────┘
   │ 写 freshness_date    │      │  ⑦ status='indexed'      │               │
   └────────────────────┘      └────────────┬─────────────┘               │
                                            │                              │
                                            ▼                              ▼
                                  ┌──────────────────┐          ┌──────────────────┐
                                  │  Milvus 向量库     │          │  DocChunk (MySQL) │
                                  │  campus_knowledge │          │  chunk 正文       │
                                  └────────┬─────────┘          └────────┬─────────┘
                                           │                              │
                                           └──────────────┬──────────────┘
                                                          ▼
                                            ┌────────────────────────────┐
                                            │   utils/rag.py retrieve()   │
                                            │   rag.py:1093               │
                                            │   + authorized_knowledge_   │
                                            │     docs() 仅 visibility     │
                                            │   + status='indexed' 过滤    │
                                            └──────────────┬─────────────┘
                                                           ▼
                              ┌────────────────────────────────────────────┐
                              │  RAG / Chat / Citation / SSE references      │
                              │  chat/views.py  utils/sse.py                 │
                              └──────────────┬─────────────────────────────┘
                                             ▼
                              ┌────────────────────────────────────────────┐
                              │            用户最终看到内容                   │
                              └────────────────────────────────────────────┘

   ★ 全仓库唯一的安全门，且只检查 oss_key 前缀
```

### 4.2 旁路总览（与统一 task 的关系）

| 路径 | 是否经过 `index_document_task`？ | 是否经过 `check_indexable`？ | 说明 |
|------|--------------------------------|---------------------------|------|
| `FileUploadView` → safe_delay | ✅ | ✅ | views.py:1500 |
| `BatchFileUploadView` → safe_delay | ✅ | ✅ | views.py:1720 |
| `TextUploadView` → safe_delay | ✅ | ✅ | views.py:1056 |
| `UploadCallbackView` → safe_delay | ✅ | ✅ | views.py:786 |
| chunked upload 合并 → safe_delay | ✅ | ✅ | chunked_views.py:398 |
| `crawler.save_page_as_knowledge_doc` → safe_delay | ✅ | ✅ | crawler.py:491 |
| `config/celery.py` 历史任务名转发 | ✅ | ✅ | celery.py:89（转发到同一 task） |
| `scripts/index_all_docs.py` | ✅（`task.run`） | ✅ | 继承门 |
| `scripts/index_quick.py` | ✅（`task.run`） | ✅ | 继承门 |
| **`scripts/seed_test_docs.py:857`** | ❌ **完全绕过** | ❌ **完全绕过** | **直接写 Milvus + DocChunk** |
| **`scripts/seed_e2e.py:95`** | ❌ | ❌ | 伪造 `status='indexed'`，无向量无 chunk |
| `scripts/rebuild_milvus.py:24` | ❌ | ❌ | 只删建空 collection，不索引 |
| **`apps/faq/tasks.py:242`** | ❌ | ❌ | **直接读 OSS → LLM** |
| **`apps/knowledge/extractor.py:414`** | ❌ | ❌ | **直接读 OSS → LLM**（被所有上传路径自动触发） |
| `scripts/seed_data.py` | — | — | 不涉及 KnowledgeDoc |

---

## 5. Complete Consumer Inventory

共识别 **31 个 KnowledgeDoc 消费点**（按"是否产生下游安全影响"排序）。

### 5.1 写向量 / 写分块（CRITICAL 面）

| # | 文件:行 | 函数 | 输入 | 输出 | OSS | DB | DocChunk | Milvus | LLM | moderation gate | storage gate | status gate |
|---|---------|------|------|------|-----|----|----------|--------|-----|-----------------|--------------|-------------|
| C1 | [tasks.py:111](backend/apps/knowledge/tasks.py#L111) | `index_document_task` | doc_id | Milvus 向量 + DocChunk + `status='indexed'` | 读 | 读写 | 写 | **写** | ✅ embed | ❌（仅前缀） | ❌ | ❌ |
| C2 | [seed_test_docs.py:857](backend/scripts/seed_test_docs.py#L857) | `index_seed_docs` | 内存文本 | 同 C1 全套 | ❌ | 读写 | 写 | **写** | ✅ embed | ❌ | ❌ | 自带写 `indexed` |
| C3 | [index_all_docs.py:105](backend/scripts/index_all_docs.py#L105) | `index_all_docs` | `status='uploaded'` | 委托 C1 | 间接 | 读写 | 间接 | 间接 | 间接 | ✅ 继承 C1 | ✅ 继承 | 只看 uploaded |
| C4 | [index_quick.py:25](backend/scripts/index_quick.py#L25) | 顶层脚本 | 前 3 篇 uploaded | 委托 C1 | 间接 | 间接 | 间接 | 间接 | 间接 | ✅ 继承 C1 | ✅ 继承 | 只看 uploaded |
| C5 | [rebuild_milvus.py:24](backend/scripts/rebuild_milvus.py#L24) | `rebuild_collection` | — | drop + 建空 collection | ❌ | ❌ | ❌ | **删** | ❌ | ❌ | ❌ | ❌ |
| C6 | [seed_e2e.py:95](backend/seed_e2e.py#L95) | `seed_docs` | 硬编码 | 伪造 `status='indexed'` + `chunk_count=8` | ❌ | 写 | ❌ | ❌ | ❌ | ❌ | ❌ | **伪造** |

### 5.2 检索 / RAG（CRITICAL 面）

| # | 文件:行 | 函数 | 输入 | 输出 | OSS | DB | DocChunk | Milvus | LLM | moderation gate | storage gate | status gate |
|---|---------|------|------|------|-----|----|----------|--------|-----|-----------------|--------------|-------------|
| C7 | [rag.py:1093](backend/utils/rag.py#L1093) | `retrieve` | question + user_context | Top-K 检索结果 → LLM 上下文 | ❌ | 读 | ✅ | **读** | 间接 | ❌ | ❌ | ✅ `indexed` |
| C8 | [rag.py:999](backend/utils/rag.py#L999) | `retrieve_images` | question | 图片 doc + preview_url | ❌ | 读 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |
| C9 | [rag.py:808](backend/utils/rag.py#L808) | `authorized_knowledge_docs` | user_context | 授权 QuerySet | ❌ | 读 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌（**仅 visibility**） |
| C10 | [rag.py:955](backend/utils/rag.py#L955) | `_fallback_title_search` | question | 标题/描述降级结果 | ❌ | 读 | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |
| C11 | [rag.py:640](backend/utils/rag.py#L640) | `_chunk_content_search` | keywords | DocChunk 正文 | ❌ | 读 | **读** | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |
| C12 | [rag.py:607](backend/utils/rag.py#L607) | `_keyword_table_search` | keywords | Keyword→doc | ❌ | 读 | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |
| C13 | [rag.py:847](backend/utils/rag.py#L847) | `_apply_hard_filters` | filters | 候选 doc_id（喂给 Milvus expr） | ❌ | 读 | ❌ | 间接 | ❌ | ❌ | ❌ | ✅ `indexed` |
| C14 | [rag.py:882](backend/utils/rag.py#L882) | `_filter_by_permission` | hits | 过滤后 hits | ❌ | 读 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |
| C15 | [rag.py:910](backend/utils/rag.py#L910) | `_enrich_with_doc_meta` | hits | 补元信息 | ❌ | 读 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ `indexed` |

### 5.3 LLM 消费（CRITICAL 面）

| # | 文件:行 | 函数 | 输入 | 输出 | OSS | DB | DocChunk | Milvus | LLM | moderation gate | storage gate | status gate |
|---|---------|------|------|------|-----|----|----------|--------|-----|-----------------|--------------|-------------|
| C16 | [extractor.py:414](backend/apps/knowledge/extractor.py#L414) | `extract_freshness_for_doc` | **doc_id** | `freshness_date` | **读全文** | 读+写 | ❌ | ❌ | **✅** | ❌ | ❌ | **❌ 无任何过滤** |
| C17 | [tasks.py:36](backend/apps/knowledge/tasks.py#L36) | `extract_freshness_task` | doc_id | 委托 C16 | 间接 | 间接 | ❌ | ❌ | 间接 | ❌ | ❌ | ❌ |
| C18 | [faq/tasks.py:242](backend/apps/faq/tasks.py#L242) | `generate_faq_from_website` | website/public 文档 | FAQItem(draft) | **读全文** | 读+写 | ❌ | ❌ | **✅** | ❌ | ❌ | ✅ `uploaded`+`indexed` |
| C19 | [faq/tasks.py:37](backend/apps/faq/tasks.py#L37) | `generate_faq_weekly` | ChatMessage | FAQItem(draft) | ❌ | 读+写 | ❌ | ❌ | **✅** | ❌ | ❌ | ❌（**间接**：消费 AI 回答） |

### 5.4 访问 / 下载 / 预览（HIGH 面）

| # | 文件:行 | 函数 | 输入 | 输出 | OSS | DB | moderation gate | storage gate | status gate |
|---|---------|------|------|------|-----|----|-----------------|--------------|-------------|
| C20 | [views.py:405](backend/apps/knowledge/views.py#L405) | `KnowledgeDocDownloadView` | doc id | **OSS 签名下载 URL** | 签名 | 读 | ❌ | ❌ | **❌** |
| C21 | [views.py:1069](backend/apps/knowledge/views.py#L1069) | `KnowledgeDocPreviewView` | doc id | 文本原文 / **预览 URL** | **读全文** | 读 | ❌ | ❌ | **❌** |
| C22 | [data_query.py:188](backend/utils/data_query.py#L188) | `find_user_docs` | user + 条件 | 元数据 + **下载 URL** | 签名 | 读 | ❌ | ❌ | **❌ 完全无 status 过滤** |
| C23 | [views.py:89](backend/apps/knowledge/views.py#L89) | `KnowledgeDocListView` | 查询参数 | 文档列表（含 `oss_url`） | 签名 | 读 | ❌ | ❌ | ❌ |
| C24 | [views.py:226](backend/apps/knowledge/views.py#L226) | `KnowledgeDocDetailView` | doc id | 完整对象（含 `oss_url`） | 签名 | 读 | ❌ | ❌ | ❌ |
| C25 | [serializers.py:54,62](backend/apps/knowledge/serializers.py#L54) | `get_oss_url` / `get_preview_url` | obj | 签名 URL | 签名 | ❌ | ❌ | ❌ | ❌ |
| C26 | [sse.py:267](backend/utils/sse.py#L267) | `build_references_events` | 检索结果 | `download_url` | 签名 | 读 | ❌ | ❌ | 间接（来自 C7） |
| C27 | [sse.py:321](backend/utils/sse.py#L321) | `build_images_events` | 图片结果 | `preview_url` | 签名 | ❌ | ❌ | ❌ | 间接（来自 C8） |
| C28 | [chat/views.py:178](backend/apps/chat/views.py#L178) | `MessageViewSet.references` | message id | 引用文档元数据 | ❌ | 读 | ❌ | ❌ | ✅ `indexed` + admin scope |

### 5.5 元数据 / 统计（INFO 面）

| # | 文件:行 | 函数 | 说明 |
|---|---------|------|------|
| C29 | [admin_panel/services/dashboard.py:380](backend/apps/admin_panel/services/dashboard.py#L380) | 仪表盘聚合 | 仅 `count` / `Sum`，无内容外泄 |
| C30 | [utils/context.py:51](backend/utils/context.py#L51) | 上下文聚合 | 仅计数 |
| C31 | [data_query.py:17,54,129,162](backend/utils/data_query.py#L17) | `count_user_docs` 等 | 仅元数据，无 `oss_key`，无下载 URL |
| C32 | [resources/models.py:17](backend/apps/resources/models.py#L17) | `UploadLog.knowledge_doc` | 仅外键日志 |

### 5.6 删除路径

| # | 文件:行 | 函数 | 是否清理 OSS | 是否清理 DocChunk | 是否清理 Milvus |
|---|---------|------|-------------|------------------|-----------------|
| C33 | [views.py:285](backend/apps/knowledge/views.py#L285) | `KnowledgeDocDeleteView` | ✅ `oss_client.delete` | ✅ CASCADE | **❌ 明确不删（代码注释 TODO）** |

---

## 6. index_document_task Audit

### 6.1 调用链

`apps/knowledge/tasks.py:111`，`@shared_task(bind=True, max_retries=2, default_retry_delay=60)`。

```
index_document_task(doc_id)
  ├─ tasks.py:139   KnowledgeDoc.objects.get(id=doc_id)         ← 找不到则 return None
  ├─ tasks.py:147   check_indexable(doc)                        ★ 唯一的门
  │    └─ 不通过 → status='failed' + index_error + return None
  ├─ tasks.py:174   extract_text_from_oss(doc.oss_key, ...)     ← 读 OSS 全文
  ├─ tasks.py:187   chunk_document(text, 500, 100)
  ├─ tasks.py:201   llm_client.embed(texts)                     ← 调 LLM Embedding
  ├─ tasks.py:210   milvus_client.delete_by_doc(doc_id)         ← 先清旧向量
  ├─ tasks.py:211   DocChunk.objects.filter(doc_id).delete()
  ├─ tasks.py:225   milvus_client.insert_chunks(...)            ← 写 Milvus
  ├─ tasks.py:240   DocChunk.objects.bulk_create(...)           ← 写 MySQL
  └─ tasks.py:243   doc.status = 'indexed'
```

### 6.2 谁调用它？

**调用（6 条业务路径 + 2 条脚本路径 + 1 条兼容转发）：**

1. `views.py:786` — `UploadCallbackView`
2. `views.py:1056` — `TextUploadView`
3. `views.py:1500` — `FileUploadView`
4. `views.py:1720` — `BatchFileUploadView`
5. `chunked_views.py:398` — 分片上传合并
6. `crawler.py:491` — 官网爬虫
7. `index_all_docs.py:105` — `index_document_task.run(None, doc.id)`
8. `index_quick.py:25` — 同上
9. `config/celery.py:89` — 历史任务名 `campus_knowledge.index_document` 转发

**不调用它（绕过）：**

10. `seed_test_docs.py:857 index_seed_docs` — 自建全套流水线
11. `seed_e2e.py:95` — 伪造 `status='indexed'`
12. `rebuild_milvus.py` — 不索引

### 6.3 当前 `check_indexable()` 到底检查什么？

[moderation.py:131-162](backend/apps/knowledge/moderation.py#L131)，全部逻辑只有两条：

```python
oss_key = (doc.oss_key or '').strip()
if not oss_key:
    return False, '文档没有关联的 OSS 对象'
if not oss_key.startswith(INDEXABLE_OSS_KEY_PREFIX):   # 'knowledge/'
    return False, 'OSS 对象不在知识库路径下'
return True, ''
```

### 6.4 逐条回答任务书的 13 个问题

| # | 问题 | 答案 | 依据 |
|---|------|------|------|
| 1 | 谁调用它？ | 见 §6.2，6 业务 + 2 脚本 + 1 转发 | grep |
| 2 | 哪些地方直接调用？ | `views.py` ×4、`chunked_views.py` ×1、`crawler.py` ×1 | grep |
| 3 | 哪些地方没有调用？ | `seed_test_docs.py`、`seed_e2e.py`、`rebuild_milvus.py`、`faq/tasks.py`、`extractor.py` | grep |
| 4 | task 开头检查什么？ | 仅 `oss_key` 非空 + 前缀为 `knowledge/` | moderation.py:145-148 |
| 5 | 是否检查 moderation_state / verdict / storage_state / ContentModeration？ | **四个全不检查** | moderation.py:131-162 |
| 6 | ContentModeration 不存在会怎样？ | **继续执行**（`check_indexable` 不查询该表） | moderation.py:145-148 |
| 7 | `moderation_state=pending` 会继续吗？ | **会继续** | 同上 |
| 8 | `moderation_state=scanning` 会继续吗？ | **会继续** | 同上 |
| 9 | `moderation_state=unavailable` 会继续吗？ | **会继续** | 同上 |
| 10 | `moderation_state=rejected` 会继续吗？ | **会继续** | 同上 |
| 11 | `storage_state=quarantine` 会继续吗？ | **会继续** | 同上 |
| 12 | `storage_state=promoted` 会继续吗？ | **会继续** | 同上 |
| 13 | `approved` + `promoted` 会继续吗？ | **会继续** | 同上 |

### 6.5 输出

```
CURRENT INDEX GATE:
FAIL

原因：check_indexable() 只做 OSS Key 前缀校验，不读取 ContentModeration，
      对 state / verdict / storage_state 一律放行。没有任何审核语义。

FUTURE REQUIRED INDEX GATE:
    storage_state == promoted
    AND
    moderation_state in {approved, confirmed_unchecked}

并且必须明确：
    status == 'indexed' 只能代表"已经索引"，不能替代 moderation_state。
    任何把 status 当作审核结论的写法都是错误的——参见 §17。
```

### 6.6 一个必须记录的正面事实

`check_indexable` 放在**任务体内**而非视图层，这个位置选择是正确的：上述 9 条调用路径**全部会自动继承**这道门。一旦把真正的审核判断填进 [moderation.py:151-160](backend/apps/knowledge/moderation.py#L151) 标注的接入点，**上传链路 + 爬虫 + 两个索引脚本会同时被收口，无需改任何调用方**。这是 P1-2C 成本最低的收口点。

---

## 7. seed_test_docs.py Audit

### 7.1 结论

```
CRITICAL BYPASS — 确认
```

### 7.2 完整调用链

`scripts/seed_test_docs.py`，CLI 入口在 [:995](backend/scripts/seed_test_docs.py#L995)。

```
python scripts/seed_test_docs.py --index
  │
  ├─ create_seed_docs()                       :797
  │    └─ KnowledgeDoc.objects.create(status='uploaded')   :835
  │         └─ doc.oss_key = f"seed/{title}.md"             :849  ← 注意：不是 knowledge/ 前缀！
  │
  └─ ensure_vector_db()                        :973
       └─ milvus_client.connect() + ensure_collection()
  │
  └─ index_seed_docs(created_docs)             :857   ★★ 绕过点在
       └─ for each (doc, content):                     ★★
            ├─ doc.status = 'chunking'                             :878
            ├─ raw_chunks = chunk_document(content, 500, 100)      :881   ← 内存文本，不读 OSS
            ├─ doc.status = 'embedding'                            :891
            ├─ embeddings = llm_client.embed(texts)                :895   ← 直接调 LLM
            ├─ vector_ids = milvus_client.insert_chunks(...)       :916   ← 直接写 Milvus
            ├─ DocChunk.objects.bulk_create(doc_chunks)            :932   ← 直接写 MySQL
            └─ doc.status = 'indexed'                              :936   ← 直接伪造终态
```

### 7.3 它绕过了什么

| 绕过目标 | 是否绕过 | 证据 |
|----------|---------|------|
| `index_document_task` | ✅ 完全绕过 | 全文件无 `index_document_task` 引用 |
| `check_indexable` | ✅ 完全绕过 | 全文件无 `check_indexable` 引用 |
| `ContentModeration` | ✅ 完全绕过 | 全文件无任何 moderation 引用 |
| `storage_state` | ✅ 完全绕过 | 全文件无 `storage_state` |
| `quarantine` | ✅ 完全绕过 | 全文件无 `quarantine` |

### 7.4 为什么它能绕过

不是"忘了加门"，而是**它必须绕过**——否则连自己都跑不起来：

`create_seed_docs` 在 [:849](backend/scripts/seed_test_docs.py#L849) 把 `oss_key` 写成：

```python
doc.oss_key = f"seed/{item['title']}.md"
```

**`seed/` 不是 `knowledge/` 前缀。** 如果 `index_seed_docs` 改成调用 `index_document_task`，`check_indexable` 会在第二行判定 `'OSS 对象不在知识库路径下'` 并直接拒绝，把 `status` 置为 `failed`。也就是说，**种子文档之所以能被索引，正是因为它跳过了那道门。**

这也顺带说明：种子文档的 `oss_key` 指向的 `seed/…` 对象在 OSS 里**根本不存在**（内容只存在于脚本内存），所以它也无法走"读 OSS → 解析"的正规流程。

### 7.5 与既有安全语义的冲突

`seed/` 前缀的对象不来自本系统任何正常上传链路，而 `check_indexable` 的设计意图恰恰是"前缀之外的 Key 不来自本系统的正常上传链路"（[moderation.py:36-38](backend/apps/knowledge/moderation.py#L36)）。**种子脚本正是在制造该门要拒绝的那类文档。**

### 7.6 修复方向（**本阶段不实施，仅记录**）

- 让 `index_seed_docs` 改为 `docs` 文本落地到 `knowledge/...`（真实 OSS 对象）后调用 `index_document_task`；
- 或为"系统种子内容"定义一条**显式的、可审计的**白名单通道，而不是静默复制一套流水线；
- 无论哪种，都必须让种子内容同样经过 moderation，否则它就是生产可用的审核后门。

**本阶段不做任何修改、不删除、不改成调用 task。**

---

## 8. index_all_docs.py Audit

### 8.1 行为

`scripts/index_all_docs.py`：

- [:70](backend/scripts/index_all_docs.py#L70) 统计 `uploaded` / `indexed` / `failed`
- [:93](backend/scripts/index_all_docs.py#L93) `docs = KnowledgeDoc.objects.filter(status='uploaded').order_by('id')`
- [:105](backend/scripts/index_all_docs.py#L105) `index_document_task.run(None, doc.id)`（同步，绕 Celery `__call__`）

### 8.2 逐项确认

| 检查项 | 结果 |
|--------|------|
| 查询哪些 KnowledgeDoc | `status='uploaded'` 全部，无 visibility 限制、无 college 限制 |
| 是否检查 status | ✅ 是（只取 `uploaded`） |
| 是否检查 visibility | ❌ 否 |
| 是否检查 moderation | ❌ 否 |
| 是否检查 storage | ❌ 否 |
| 是否直接调用 `index_document_task` | ✅ 是（`task.run`） |
| 是否直接进行 embedding | ❌ 否（委托 task） |
| 是否直接写 Milvus | ❌ 否（委托 task） |
| 是否可能重新索引历史未审核文档 | ✅ 是——**但它继承了 task 内的门** |

### 8.3 风险评级

```
RISK LEVEL: MEDIUM
```

**理由（正面）**：该脚本**不复制流水线**，而是复用 `index_document_task`。它因此**自动继承** `check_indexable`。当 P1-2C 把真正的审核判断填进那个接入点时，本脚本**无需任何修改**就同步被收口。这是本脚本与 `seed_test_docs.py` 的本质区别。

**理由（为何仍是 MEDIUM 而非 LOW）**：

1. 它按 `status='uploaded'` 选文档，**对 `status='indexed'` 的历史文档无能为力**——而恰恰是这批已入库文档承载了"存量未审核"的风险（见 §16）。想重扫它们，需要另写路径。
2. 它**没有分批 / 限流**，一次遍历全部 `uploaded` 文档，`task.run` 同步执行意味着全量解析 + Embedding 串行跑完。
3. 它绕过了 Celery，`task.run(None, doc_id)` 传入 `self=None`：任务体内 `max_retries = ... if self is not None else 0`（tasks.py:159）→ `max_retries=0` → **任何一次失败立即判定为最终失败**，把 `status` 打成 `failed`，没有重试机会。

### 8.4 未来建议的统一门禁位置

**不要在这个脚本里加门。** 门必须留在 `index_document_task` 内（现已如此），脚本只负责"选哪些文档来跑"。这个分工是正确的，P1-2C 应保持。

---

## 9. rebuild_milvus Audit

### 9.1 逐项确认

`scripts/rebuild_milvus.py:24 rebuild_collection()`：

| # | 检查项 | 结果 |
|---|--------|------|
| 1 | 数据来源 | **无**。不读取任何文档 |
| 2 | 是否读取 KnowledgeDoc | ❌ 否 |
| 3 | 是否读取 DocChunk | ❌ 否 |
| 4 | 是否直接读取 OSS | ❌ 否 |
| 5 | 是否重新生成 embedding | ❌ 否 |
| 6 | 是否写 Milvus | ❌ 否（只 `drop()` + `ensure_collection()` 建**空**集合） |
| 7 | 是否检查 moderation | ❌ 否 |
| 8 | 是否检查 storage_state | ❌ 否 |
| 9 | 是否检查 status | ❌ 否 |

### 9.2 判断

**"Milvus rebuild" 本身不是审核绕过。** 它不做任何内容消费，也不产生任何新的 RAG 数据。它只做一件事：`Collection('campus_knowledge').drop()` 然后重建一个空集合。

**但它是数据不一致的放大器**，风险来自它**不做什么**：

- 它**不删除** `DocChunk` 表数据；
- 它**不重置** `KnowledgeDoc.status`（仍为 `indexed`）；
- 它**不重建**任何向量。

执行后系统进入这种状态：

```
KnowledgeDoc.status = 'indexed'   ← 仍然声称"已索引"
DocChunk 行                        ← 仍然存在
Milvus                             ← 空的
```

此时 `utils/rag.py:retrieve()` 的向量通道返回 0 条（Milvus 空），但 `_chunk_content_search`（C11）和 `_keyword_search`（C10）**仍然会命中**并返回正文内容。也就是说：**检索结果会静默地从"向量 + 关键词"降级为"纯关键词"，没有任何告警**，而 `status='indexed'` 依然是 `indexed`。

### 9.3 风险评级

```
RISK LEVEL: MEDIUM   （数据一致性 / 可观测性，非审核绕过）
```

**未来建议**：P1-2C 若引入"按审核状态重建索引"，必须让该流程同时处理 `DocChunk` 与 `status`，不能只动 Milvus。并且在 `storage_state` 落地后，重建应当只重建 `promoted` 文档。

---

## 10. FAQ Consumer Audit

### 10.1 结论

```
CRITICAL — LLM CONSUMPTION BYPASS（知识消费者绕过）
```

这不是传统意义上的"索引绕过"（不写 Milvus），而是**"LLM 消费绕过"**：文档在被审核之前，全文已经被送进 LLM。

### 10.2 证据

[apps/faq/tasks.py:242](backend/apps/faq/tasks.py#L242) `generate_faq_from_website`：

```python
# :264
saved_count = crawl_and_index(max_pages=max_pages)
# :265-269  ★ 唯一的筛选条件
docs = list(
    KnowledgeDoc.objects.filter(
        source_type='website',
        visibility='public',
        status__in=['uploaded', 'indexed'],
    ).order_by('-created_at')[:max_docs]
)
# :282
for doc in docs:
    if doc.id in processed_doc_ids:
        continue
    # :287  ★ 读 OSS 全文
    text = extract_text_from_oss(doc.oss_key, doc.file_type, doc.file_name)
    # :288  ★ 全文送 LLM
    result = generate_faq_from_source(doc.title, text, doc.source_url)
    # :291  生成 FAQItem(status='draft')
```

**筛选条件只有三项：`source_type` / `visibility` / `status__in`。没有任何 moderation 或 storage 判断。**

### 10.3 逐状态回答（任务书 §10）

| KnowledgeDoc 状态 | FAQ pipeline 是否仍可能消费？ | 依据 |
|-------------------|------------------------------|------|
| `moderation_state = pending` | **是** | `status__in` 只看 `uploaded`/`indexed` |
| `moderation_state = scanning` | **是** | 同上 |
| `moderation_state = rejected` | **是** | 同上（只要 `status` 没被改成别值） |
| `moderation_state = unavailable` | **是** | 同上 |
| `storage_state = quarantine` | **是** | 代码中完全没有 `storage_state` |

**答案：YES。全部四种状态 + quarantine 都能被消费。**

### 10.4 爆炸半径

`source_type='website'` 把范围限定在爬虫文档。但爬虫 [crawler.py:463-480](backend/utils/crawler.py#L463) 对**每一个抓取到的页面**都写 `source_type='website'` + `visibility='public'` + `status='uploaded'`。也就是说，**所有爬取到的内容都是候选**，只要 `crawl_and_index` 跑过，它们就会在同一个任务里被立刻送进 LLM。

而且触发路径很轻：`apps/faq/views.py:397` 的 API 触发 → `safe_delay(generate_faq_from_website, log_id=log.id)`。

### 10.5 未来必须在 FAQ 消费入口增加

```python
storage_state == promoted
AND
moderation_state in {approved, confirmed_unchecked}
```

**本阶段不修改 FAQ。** 具体接入位置见 §20。

---

## 11. find_user_docs Audit

### 11.1 结论

```
ACCESS / READ PATH BYPASS — 确认
```

### 11.2 证据

[utils/data_query.py:188-280](backend/utils/data_query.py#L188)：

```python
def find_user_docs(user, keywords=None, days=None, category=None,
                   file_type=None, limit=10):
    # :213-230  权限范围：纯角色判断
    if user_role == 'super_admin':
        qs = KnowledgeDoc.objects.all()
    elif user_role in ('admin', 'college_admin'):
        qs = KnowledgeDoc.objects.filter(...)
    elif user_role == 'dept_admin':
        qs = KnowledgeDoc.objects.filter(...)
    elif user_role:
        qs = KnowledgeDoc.objects.filter(uploader=user)
    else:
        qs = KnowledgeDoc.objects.none()

    # :232-251  关键词 / 时间 / 分类 / 文件类型
    ...
    qs = qs.select_related("category").order_by("-created_at")[:limit]

    # :255-264  ★ 生成真实签名下载 URL
    def _get_dl_url(doc) -> str:
        if doc.oss_key:
            url = oss_client.get_download_url(
                doc.oss_key, expires=3600, file_name=doc.file_name,
            )
            if url:
                return url
        return ''
```

### 11.3 逐项确认

| 检查项 | 结果 |
|--------|------|
| 直接返回 KnowledgeDoc | ✅ 是（序列化为 dict） |
| 检查 status | **❌ 否——完全没有 status 条件** |
| 检查 moderation_state | ❌ 否 |
| 检查 storage_state | ❌ 否 |
| 返回 OSS URL | ✅ 是，`download_url`，有效期 3600 秒 |
| 返回 document metadata | ✅ 是（title / description / file_type / file_size / category / created_at） |
| 被其他接口继续消费 | ✅ 是——经 `utils/query_router.py` 路由供聊天"文件管理"类提问使用 |

### 11.4 与其它读取路径的对比

这是本审计中**唯一一个连 `status='indexed'` 都不施加**的文档读取路径：

| 路径 | status 过滤 |
|------|-------------|
| `KnowledgeDocListView` | ❌ 无（仅 visibility） |
| `KnowledgeDocDownloadView` | ❌ 无（仅 visibility） |
| `KnowledgeDocPreviewView` | ❌ 无（仅 visibility） |
| `find_user_docs` | ❌ 无（仅角色） |
| RAG `retrieve` | ✅ `indexed` |
| `chat MessageViewSet.references` | ✅ `indexed` |

`super_admin` 走 `KnowledgeDoc.objects.all()` —— 对超管返回**全部文档 + 每个文档 1 小时有效的签名下载 URL**，不区分 `status='failed'` / `status='parsing'`。

### 11.5 未来风险（quarantine 落地后尤为严重）

一旦上传链路改为 `upload → quarantine`，`doc.oss_key` 可能指向 `quarantine/…`。**本函数会直接为隔离区对象生成有效的签名下载 URL 并返回给调用方**——这将成为"隔离区内容可被读取"的具体实现路径。

**本阶段不修复。** 未来门禁位置见 §20。

---

## 12. RAG / Chat / Citation Audit

### 12.1 逐条回答任务书 §12 的 A–I

| # | 问题 | 答案 | 证据 |
|---|------|------|------|
| A | 是否直接查询 Milvus？ | **是**，唯一一处：`rag.py:1143` `milvus_client.search(q_emb, top_k, filter_ids=candidate_ids)` | rag.py:1143 |
| B | 如何保证对应 KnowledgeDoc 已通过审核？ | **无任何保证。** 只保证 `status='indexed'`（经 `candidate_ids` 硬过滤） | rag.py:856, 1144 |
| C | 是否通过 DocChunk → KnowledgeDoc 回溯？ | **是**，两处：`_chunk_content_search`（rag.py:668 `DocChunk.objects.filter(doc__in=allowed_docs)`）与 `_select_relevant_chunk`（rag.py:505） | rag.py:505, 668 |
| D | 回溯时是否检查 moderation_state？ | **❌ 否** | 同上 |
| E | 是否检查 storage_state？ | **❌ 否** | 同上 |
| F | 已 rejected 但旧 Milvus 向量仍存在？ | **是，会出现。** 见 §12.2 | views.py:302 |
| G | 重新上传 / 替换 OSS 但旧 embedding 仍在？ | **是。** `delete_by_doc` 只在"重新成功索引时"被调（tasks.py:210）；若新索引失败或走的是 seed 路径，旧向量留存。且 **ChromaDB 降级模式下 `delete_by_doc` 是空操作**（milvus.py:299 `return True`） | tasks.py:210, milvus.py:299 |
| H | 删除文档后 Milvus 是否一定删除？ | **否。** `KnowledgeDocDeleteView` 代码注释明写"**Milvus 向量暂不同步删除（TODO，甲同学完成后集成）**" | views.py:302 |
| I | moderation reject 后已有 Milvus 数据是否继续参与 RAG？ | **视 reject 的实现而定。** 当前不存在 moderation 决策代码；若未来"reject"只写 `ContentModeration` 而不改 `KnowledgeDoc.status`，则文档保持 `status='indexed'`，**继续参与 RAG** | 无相关代码 |

### 12.2 F / H 的详细展开（本阶段核心风险之一）

**Milvus 向量的生命周期是全仓库最薄弱的一环。** 向量的删除只有两个触发点：

1. `tasks.py:210` `milvus_client.delete_by_doc(doc_id)` —— 在下一次**成功**索引开始时执行；
2. `seed_test_docs.py:826` `milvus_client.delete_by_doc_id(did)` —— **这个方法不存在**。

**第 2 点是一个真实的缺陷**：`MilvusClient` 只定义了 `delete_by_doc`（[milvus.py:292](backend/utils/milvus.py#L292)），没有 `delete_by_doc_id`。因此 `seed_test_docs.py --force` 在删除旧种子文档时，这一行会抛 `AttributeError`，而它被包在：

```python
try:
    milvus_client.connect()
    for did in old_ids:
        milvus_client.delete_by_doc_id(did)
except Exception as e:
    logger.warning(f'删除 Milvus 向量时出错（可忽略）: {e}')
```

异常被吞，日志写"可忽略"。**结果：`--force` 重建种子文档时，旧向量永远留在 Milvus 里**，与新向量（`vector_id` 相同，均为 `doc_{id}_chunk_{j}`）产生重复或错位。

**第 3 个隐患**：[milvus.py:212-217](backend/utils/milvus.py#L212) 的空列表语义。

```python
expr = None
if filter_ids:                      # ← 空列表是 falsy
    valid_ids = [d for d in filter_ids if d > 0]
    if valid_ids:
        expr = f'doc_id in {valid_ids}'
```

**`filter_ids=[]` 意味着 `expr = None`，即"不过滤，检索整个 collection"。** 目前 `retrieve()` 在 [rag.py:1128](backend/utils/rag.py#L1128) 有 `if not candidate_ids: return []` 保护，所以不会发生。但这是一个**静默的、方向性错误的默认值**：未来任何调用方传空列表（本意可能是"没有候选，什么都别返回"）都会得到**全库检索**。P1-2C 加门时必须显式处理。

### 12.3 RAG 路径的门禁现状

`retrieve()` 的每一路召回都收敛到 `authorized_knowledge_docs()`（[rag.py:808](backend/utils/rag.py#L808)），而这个函数**只做 visibility 判断**：

```python
qs = KnowledgeDoc.objects.all()
if role == 'super_admin':
    return qs                       # ← 超管：全部文档，连 visibility 都不过滤
qs = qs.filter(visibility='public')
...college / department / uploader 条件...
```

各检索通道在此之上叠加 `status='indexed'`。**没有任何一路检查 moderation 或 storage。**

### 12.4 Citation / SSE

`utils/sse.py:267 build_references_events` 为每条引用生成 `download_url`（300 秒）：

```python
if doc.oss_key:
    entry['download_url'] = oss_client.get_download_url(
        doc.oss_key, expires=300, file_name=doc.file_name,
    ) or ''
```

它消费的是 `retrieve()` 的输出（已过 `status='indexed'`），所以门禁强度等同于 C7。`build_images_events`（sse.py:321）同理，消费 `retrieve_images()`。

### 12.5 一个文档串不符（INFO）

[rag.py:1010-1012](backend/utils/rag.py#L1010) 的 docstring 写：

> 注意：**不按 status='indexed' 过滤**——图片无文本可索引……

但 [rag.py:1042-1044](backend/utils/rag.py#L1044) 的代码是：

```python
qs = authorized_knowledge_docs(user_context).filter(
    file_type__in=image_types, status='indexed',     # ← 实际过滤了
)
```

代码是对的，docstring 是过期的。**仅记录，不修改。**

---

## 13. OSS Direct Read Audit

### 13.1 全部 OSS 调用点

| 分类 | 文件:行 | 调用 | 说明 |
|------|---------|------|------|
| **下载 URL** | [extractor.py:263](backend/apps/knowledge/extractor.py#L263) | `get_download_url(key, 600)` | parser 前置：下载全文 |
| | [serializers.py:54](backend/apps/knowledge/serializers.py#L54) | `get_download_url` | 列表/详情返回 `oss_url` |
| | [views.py:430](backend/apps/knowledge/views.py#L430) | `get_download_url` | 下载接口 |
| | [views.py:1144](backend/apps/knowledge/views.py#L1144) | `get_download_url(key, 60)` | 文本预览下载全文 |
| | [data_query.py:259](backend/utils/data_query.py#L259) | `get_download_url(key, 3600)` | `find_user_docs` |
| | [sse.py:267](backend/utils/sse.py#L267) | `get_download_url(key, 300)` | 聊天引用 |
| **预览 URL** | [serializers.py:62](backend/apps/knowledge/serializers.py#L62) | `get_preview_url(key, 604800)` | **7 天**有效期 |
| | [views.py:1132](backend/apps/knowledge/views.py#L1132) | `get_preview_url` | 媒体预览 |
| | [sse.py:321](backend/utils/sse.py#L321) | `get_preview_url(key, 3600)` | 图片内联 |
| **写** | [views.py:1002](backend/apps/knowledge/views.py#L1002) | `upload_text` | TextUploadView |
| | [crawler.py:451](backend/utils/crawler.py#L451) | `upload_text` | 爬虫入库 |
| | [views.py:1332](backend/apps/knowledge/views.py#L1332) | `bucket.put_object` | 直传 |
| | [views.py:1629](backend/apps/knowledge/views.py#L1629) | `bucket.put_object` | 批量直传 |
| | [chunked_views.py:186,248,324](backend/apps/knowledge/chunked_views.py#L186) | multipart | 分片上传 |
| **删除** | [views.py:312](backend/apps/knowledge/views.py#L312) | `delete` | 删除文档 |
| | [views.py:384](backend/apps/knowledge/views.py#L384) | `delete` | 批量删除 |
| | [chunked_views.py:340](backend/apps/knowledge/chunked_views.py#L340) | `delete` | 重复文件清理 |
| | [tasks.py:308](backend/apps/knowledge/tasks.py#L308) | `abort_multipart_upload` | 过期会话清理 |
| **元数据** | [views.py:870](backend/apps/knowledge/views.py#L870) | `head_object` | 回调真实性校验（P1-1） |
| | [chunked_views.py:156,338](backend/apps/knowledge/chunked_views.py#L156) | `check_duplicate` | MD5 去重 |
| | [views.py:641,731](backend/apps/knowledge/views.py#L641) | `check_duplicate` | MD5 去重 |
| **运维脚本** | [fix_oss_content_type.py:44](backend/scripts/fix_oss_content_type.py#L44) | bucket 遍历 + 修改 | 存量 Content-Type 修补 |
| | [oss_diag.py](backend/scripts/oss_diag.py) | 诊断 | 只读诊断 |

### 13.2 能否读到 `quarantine/…`？

**当前答案：没有任何代码路径会去读 `quarantine/`，因为没有任何代码会往那里写。** 全仓库 `quarantine` 字符串只出现在 P1-2A 的常量、模型字段、migration 和测试中。

**未来风险：** 上面绝大多数读取接口都只是把 `doc.oss_key` 传给 `get_download_url` / `get_preview_url`，**不校验前缀**。一旦 `oss_key` 指向 `quarantine/…`：

| 会不会泄露隔离内容？ | 路径 |
|---------------------|------|
| **会**（对超管，且无 status 过滤） | `find_user_docs` C22（`super_admin` → `objects.all()`） |
| **会**（对可见范围内的任意角色） | `KnowledgeDocDownloadView` C20、`KnowledgeDocPreviewView` C21、`KnowledgeDocListView` C23（含 `oss_url`）、`KnowledgeDocDetailView` C24、serializers C25（`preview_url` **7 天有效**） |
| 间接 | `sse.py` C26/C27（受 `retrieve()` 的 `status='indexed'` 限制） |

**结论**：未来的隔离方案若只把对象挪到 `quarantine/` 而不改这些读取接口，**隔离区内容依然可以通过签名 URL 被拿到**。这是 §20 必须收口的一组点。

### 13.3 分类小结

| 类型 | 是否读 OSS | 是否有 moderation gate | 风险 |
|------|-----------|----------------------|------|
| 下载 | ✅ | ❌ | HIGH |
| 预览 | ✅ | ❌ | HIGH |
| Parser（index） | ✅ | 仅前缀 | HIGH |
| Parser（freshness） | ✅ | **无** | CRITICAL |
| FAQ | ✅ | **无** | CRITICAL |
| RAG / Citation | ❌（只用签名 URL） | ❌ | HIGH |
| Admin 列表/详情 | ❌（返回签名 URL） | ❌ | HIGH |
| 后台任务（清理/去重） | ✅ | ❌ | LOW（不返回内容） |

---

## 14. Parser / Extractor Audit

### 14.1 解析器全集

解析逻辑**已经统一在一处**：`apps/knowledge/extractor.py`，通过 `_EXTRACTORS` 分派（[extractor.py:220-232](backend/apps/knowledge/extractor.py#L220)）。

| 格式 | 实现 | 位置 | 外部依赖 |
|------|------|------|----------|
| PDF | PyMuPDF `fitz`，回退 `pdfplumber` | extractor.py:52, 62 | 第三方库 |
| DOCX | `python-docx` | extractor.py | 第三方库 |
| PPTX | `python-pptx` | extractor.py:101 | 第三方库 |
| XLSX | `openpyxl` | extractor.py:117 | 第三方库 |
| XLS | `xlrd` | extractor.py:155 | 第三方库 |
| DOC | `antiword` → `docx2txt` → `soffice` | extractor.py:182, 190, 209 | **外部可执行程序** |
| TXT / MD / HTML 等 | 直接读取 | extractor.py:283 | — |

### 14.2 消费者关系（谁可以调用 parser）

统一入口 [extractor.py:251](backend/apps/knowledge/extractor.py#L251) `extract_text_from_oss(oss_key, file_type, file_name)`。调用方只有三个：

| 调用方 | 位置 | 触发时机 | 有 moderation gate？ |
|--------|------|----------|---------------------|
| `index_document_task` | [tasks.py:174](backend/apps/knowledge/tasks.py#L174) | 上传后自动 | 仅 `check_indexable` 前缀 |
| `extract_freshness_for_doc` | [extractor.py:434](backend/apps/knowledge/extractor.py#L434) | **上传后自动** | **无** |
| `generate_faq_from_website` | [faq/tasks.py:287](backend/apps/faq/tasks.py#L287) | 手动 / 定时 | **无** |

此外 [views.py:1365](backend/apps/knowledge/views.py#L1365) 直接 `from apps.knowledge.extractor import _EXTRACTORS`（私有名），且 [views.py:1264](backend/apps/knowledge/views.py#L1264) 定义了 `DOCUMENT_EXTRACTABLE_TYPES` —— 说明视图层也直接掌握了解析能力，未走统一入口。

### 14.3 隐患（逐条确认）

| 问题 | 答案 | 证据 |
|------|------|------|
| 哪些 consumer 可直接调用 parser？ | 上述 3 个 + `views.py`（私有名直引） | 见 14.2 |
| 哪些 parser 可在 moderation 之前被调用？ | **全部三个调用点都在 moderation 之前**（因为 moderation 目前不存在） | — |
| 是否读取整个 OSS 对象？ | **是**，[extractor.py:277](backend/apps/knowledge/extractor.py#L277) `f.write(resp.content)` —— **一次性全量读入内存，无任何大小上限** | extractor.py:274-277 |
| 是否读取本地临时文件？ | 是，`tempfile.mkstemp`（:271），`finally` 中删除（:291） | extractor.py:271-293 |
| 是否调用外部程序？ | **是**：`subprocess.run(['antiword', ...])`（:182）、`soffice --headless --convert-to txt`（:209） | extractor.py:182, 209 |
| 是否产生文本？ | 是 | — |
| 是否产生 DocChunk？ | 间接（由 `index_document_task` 在解析后分块） | tasks.py:187 |
| 是否直接进入 LLM？ | **是**，`extract_freshness_for_doc` 与 `generate_faq_from_website` 都在解析后直接送 LLM | extractor.py:442, faq/tasks.py:288 |

### 14.4 关键结论

> **审核服务未来应该统一调用 extractor，而不是让各业务模块各自解析。**

现状**好于**这句话的描述：解析实现确实已经集中在 `extractor.py`。**问题不是"各自解析"，而是"谁都解析"** —— 同一个 `extract_text_from_oss` 被 3 个调用点以 3 种不同的门禁强度调用，其中 2 个完全无门禁。

风险还在于 `antiword` / `soffice` 是**外部可执行程序**，接收的是**未经任何恶意软件扫描的上传文件**。当前 `soffice --headless` 会在解析回调未知格式时被触发（extractor.py:209）。**P1-2C 接 ClamAV 之后，扫描必须发生在解析之前**，否则"先解析后扫描"等于让解析器替我们执行了不可信输入。

---

## 15. Crawler Audit

### 15.1 网页如何变成 KnowledgeDoc

[utils/crawler.py:400](backend/utils/crawler.py#L400) `save_page_as_knowledge_doc`：

```
爬取页面 → extract_text_from_html (:141)
   ↓ content < 50 字符则跳过 (:406)
   ↓ 按 source_url 去重 (:411) / 按 md5 去重 (:417)
   ↓ oss_key = f"knowledge/website/0/{now:%Y-%m}/crawl_{uuid12}.txt"   (:449)  ★ 直接写 knowledge/
   ↓ oss_client.upload_text(content, oss_key)                          (:451)
   ↓ KnowledgeDoc.objects.create(
        source_type='website', file_type='txt',
        visibility='public', scope='school',
        oss_key=oss_key, status='uploaded',        (:479)               ★ 直接进入 uploaded
     )
   ↓ safe_delay(index_document_task, doc.id)       (:491)               ★ 走统一 task
```

### 15.2 逐项确认

| 检查项 | 结果 |
|--------|------|
| 是否直接写 `knowledge/`？ | ✅ **是**，[:449](backend/utils/crawler.py#L449) `knowledge/website/0/{YYYY-MM}/…` |
| 是否创建 KnowledgeDoc？ | ✅ 是 |
| 是否直接置 `uploaded` / `indexed`？ | 创建时 `uploaded`；`indexed` 由 task 设置 |
| 是否直接调用 `index_document_task`？ | ✅ 是（经 `safe_delay`，[:491](backend/utils/crawler.py#L491)） |
| 是否存在 moderation bypass？ | **索引维度：否**（走统一 task，继承门）。**消费维度：是**（见 §10 FAQ，爬虫文档会被 `generate_faq_from_website` 直接送 LLM） |

### 15.3 内容类型区分

| 类型 | 处理方式 | 说明 |
|------|----------|------|
| **网页正文** | `extract_text_from_html` → txt → `knowledge/website/…` | 主路径，见上 |
| **附件（PDF/Office/图片）** | [:49](backend/utils/crawler.py#L49) 正则 `\.(pdf\|zip\|rar\|doc\|docx\|xls\|xlsx\|ppt\|pptx\|jpg\|jpeg\|png\|gif\|mp4\|avi)$` —— **仅用于匹配，不下载** | 附件不被抓取入库 |
| 图片 | 同上，不抓取 | 无 OCR |
| 图片 / 其他 | — | — |

### 15.4 P1-2D 必须注意的冲突

爬虫**直接写入 `knowledge/` 正式命名空间**，且 `oss_key` 以 `knowledge/` 开头。未来的门禁要求是 `storage_state == promoted`。但爬虫**永远不会设置 `storage_state`**（它根本不创建 `ContentModeration`）。

**因此在 P1-2D 落地后，如果门禁按"`storage_state == promoted`"实现，所有爬虫文档会被全部拒绝**（它们的 `ContentModeration` 不存在 / `storage_state` 未设）。这与 [moderation.py:36-38](backend/apps/knowledge/moderation.py#L36) 的注释"爬虫路径为 `knowledge/website/…`，两者都在该前缀下"所体现的既有假设直接冲突。

**必须在 P1-2D 同时改造爬虫的落地方式**（写入 `quarantine/` → 审核 → promotion），否则爬虫功能会被门禁整体打断。**本阶段不修改。**

---

## 16. Historical Documents

### 16.1 现状

migration `0008` **不含任何 `RunPython`、不含任何数据迁移**。因此：

> **现有全部 `KnowledgeDoc` 都没有 `ContentModeration` 记录。**

`ContentModeration` 是 `OneToOneField`，不存在的方向是"查不到"，不是"查到空值"。所以未来的门禁有两种写法，后果截然不同：

```python
# 写法 A（安全，但会阻断全部历史文档）
cm = ContentModeration.objects.filter(doc=doc).first()
if cm is None or not is_indexable_moderation_state(cm.state):
    return False, '尚未审核'

# 写法 B（危险：None 被当成"没问题"）
if cm and not is_indexable_moderation_state(cm.state):
    return False
```

**写法 B 是必须严禁的**——`cm is None` 恰好覆盖了全部历史文档，等于把它们全部默认为已放行。P1-2C 必须明确采用写法 A 的语义。

### 16.2 各类文档的未来处置（策略对比）

| 类别 | 数量级 | 当前状态 | LEGACY_UNCHECKED 策略 | 重新审核策略 |
|------|--------|----------|----------------------|-------------|
| **历史文档（未 indexed）** | 中 | 无 ContentModeration，`status` 各异 | 直接放行（**有风险**） | 纳入扫描队列 |
| **新上传文档** | — | 走上传链路 | 不需要 | 走新流水线 |
| **已 indexed 文档** | 大 | `status='indexed'`，**已在 Milvus + DocChunk 中** | 继续放行（**风险集中在"已在 RAG 里"**） | 需重扫 + 可能需回滚 RAG 结果 |
| **已进入 Milvus 的文档** | 大 | 向量存在 | 同上 | 同上 |
| **rejected 文档** | 0（当前） | 概念尚不存在 | — | 需从 Milvus 撤除 |
| **deleted 文档** | — | MySQL 已删，**Milvus 向量可能残留** | — | 需与 C33 的 TODO 一并处理 |

### 16.3 两种策略的风险对比

**LEGACY_UNCHECKED（打底放行）**

- 优点：不影响存量功能，RAG / FAQ / 下载全部照常，零回归。
- 风险：**系统在切换瞬间并没有变得更安全**。"已审核"与"从未审核"两种内容在 RAG 中完全不可区分，审核体系的可信度被稀释。
- **实现障碍**：P1-2A 的状态白名单只有 `{approved, confirmed_unchecked}`，**没有 `legacy_unchecked`**。要落地此策略必须**新增一个状态**——而 P1-2A 规范明确要求不得自行增加状态。因此这是一个**需要先决策、后实施**的事项。可选的替代是用 `confirmed_unchecked` 配合系统级 `confirmation_reason`，但 `confirmed_unchecked` 的语义是"人工确认放行"，用机器批量写入会**破坏该状态"必须人工产生"这一不变量**（见 P1-2A §8），需要谨慎评估。

**重新审核（分批重扫）**

- 优点：安全语义干净，`approved` 就是真的扫过。
- 风险：需要**全量重扫**（解析 + ClamAV + LLM/Vision），成本高；重扫期间存量文档的安全门是关是开需要单独决策；重扫会重写 Milvus（见 §9 的重建一致性问题）。

### 16.4 本阶段结论

**禁止在本阶段自动创建历史 moderation 记录** —— 已遵守，未创建任何记录。

**建议（供 P1-2C 决策，不实施）**：优先选择"**先建 `legacy_unchecked` 状态（或等价的可审计标记）+ 分批重扫**"的组合，即：

1. 打底标记为"未审核"而非"已放行"，**默认不放行**；
2. 建立分批重扫队列，扫完一批升一批；
3. 未扫到的存量文档在 RAG 中**降权或排除**，并对外可见性地提示"内容未完成安全审核"。

这样既不制造虚假的安全结论，也不至于一次性切断全部存量内容。

---

## 17. State Inconsistency Analysis

### 17.1 四个字段的语义

| 字段 | 归属 | 真实语义 | 谁在写 |
|------|------|----------|--------|
| `KnowledgeDoc.status` | `knowledge_doc` | **处理进度**（uploaded → parsing → chunking → embedding → indexed / failed） | `index_document_task`、各上传视图、脚本 |
| `KnowledgeDoc.visibility` | `knowledge_doc` | **可见范围**（public / private） | 上传视图、编辑接口 |
| `ContentModeration.state` | `content_moderation` | **审核结论状态** | **当前无任何写入方** |
| `ContentModeration.storage_state` | `content_moderation` | **对象所在命名空间** | **当前无任何写入方** |

**四个字段之间没有任何代码级的约束或同步机制。** 它们可以任意组合。

### 17.2 不一致组合清单

| # | 组合 | 可能吗 | 后果 |
|---|------|--------|------|
| 1 | `status=indexed` + `moderation_state=pending` | ✅ 可能（CM 不存在即等价于此） | **文档已在 RAG 中，但从未审核** —— 当前默认状态 |
| 2 | `status=uploaded` + `moderation_state=approved` | ✅ 可能 | 审核过了但没索引；RAG 取不到（`status` 门），但**下载/预览/`find_user_docs` 能拿到** |
| 3 | `storage_state=quarantine` + `moderation_state=approved` | ✅ 可能 | 已批准但对象还在隔离区 → 索引会读到隔离对象，下载会给隔离对象签名 URL |
| 4 | `storage_state=promoted` + `moderation_state=rejected` | ✅ 可能 | **对象已在正式区且已拒绝** —— 若不给 RAG 加门，直接泄露 |
| 5 | Milvus 有数据 + KnowledgeDoc 已删除 | ✅ **确定会发生** | C33 明确不删向量（views.py:302 TODO） |
| 6 | Milvus 有数据 + `rejected` | ✅ 可能 | 见 §12.2 |
| 7 | DocChunk 有数据 + `rejected` | ✅ 可能 | `_chunk_content_search`（C11）会命中 |
| 8 | `knowledge/` 有文件 + `rejected` | ✅ 可能 | 下载/预览/`find_user_docs` 都能拿到 |
| 9 | `status=indexed` + `moderation_state=unavailable` | ✅ 可能 | 扫描失败被当成干净放行 —— 正是 P1-2A 规范禁止的情形 |
| 10 | Milvus 空 + `status=indexed` | ✅ 确定会发生（`rebuild_milvus` 后） | RAG 静默降级为纯关键词检索（§9.2） |

### 17.3 真正的安全门禁是哪个字段？

**当前：没有任何字段是安全门禁。** 最接近的是 `KnowledgeDoc.status == 'indexed'`，但它表达的是"处理进度"，把它当作审核结论是**类别错误**——一份 `indexed` 文档可能是恶意内容，而一份 `rejected` 文档可能还保有 `indexed`。

**未来：`ContentModeration.state` 必须是唯一的审核真源。** `status` 只能回答"索引做了没"，`visibility` 只能回答"谁能看"，`storage_state` 只能回答"对象在哪"。

### 17.4 各行为应该由哪个字段决定（建议，不实施）

| 消费行为 | 应由什么决定 | 说明 |
|----------|-------------|------|
| **INDEX**（进 Milvus） | `state ∈ {approved, confirmed_unchecked}` **AND** `storage_state == promoted` | 由 `check_indexable` 统一收口 |
| **RAG**（检索/召回/引用） | 同上（**建议直接复用同一判断函数**） | 不能只看 `status='indexed'` |
| **FAQ**（送 LLM） | 同上 | 与 RAG 同级，属"内容消费" |
| **DOWNLOAD** | `state ∈ {approved, confirmed_unchecked}` **AND** `visibility` 通过 | |
| **PREVIEW** | 同 DOWNLOAD | |
| **READ（列表/详情元数据）** | `visibility` 通过即可；**但 `oss_url` / `preview_url` 字段必须与 DOWNLOAD 同级门禁** | 否则元数据接口变成绕道下载入口（C23/C24/C25） |

**不要修改模型。** 上述均为建议。

---

## 18. Bypass Matrix

| # | 旁路 | 入口 | 绕过对象 | 严重度 | 证据 |
|---|------|------|----------|--------|------|
| B1 | **种子脚本自建索引流水线** | `seed_test_docs.py:857` | task / check_indexable / CM / storage / quarantine | **CRITICAL** | §7 |
| B2 | **上传后自动把全文送 LLM** | `extractor.py:414` ← `tasks.py:36`（全部 6 条上传路径） | 任何审核 | **CRITICAL** | §5.3 C16/C17 |
| B3 | **FAQ 网站管线直接消费未审核文档** | `faq/tasks.py:242` | CM / storage | **CRITICAL** | §10 |
| B4 | **迁移/删除后 Milvus 向量残留** | `views.py:302`（删除不删向量）、`milvus.py:299`（Chroma 空操作）、`seed_test_docs.py:826`（方法不存在） | 数据一致性 | **HIGH** | §12.2 |
| B5 | **`find_user_docs` 无 status 过滤 + 签名下载 URL** | `data_query.py:188` | status / CM / storage | **HIGH** | §11 |
| B6 | **下载/预览/列表/详情 只做 visibility，无审核门** | `views.py:89,226,405,1069`、`serializers.py:54,62` | CM / storage | **HIGH** | §13.2 |
| B7 | **`retrieve()` 的 `filter_ids=[]` 静默等于全库检索** | `milvus.py:212-217` | 召回范围 | **HIGH**（潜在） | §12.2 |
| B8 | **爬虫直接写 `knowledge/` 且不建 CM** | `crawler.py:449,479` | storage（P1-2D 后） | MEDIUM | §15.4 |
| B9 | **`seed_e2e.py` 伪造 `status='indexed'`** | `seed_e2e.py:95` | status 语义 | MEDIUM | §5.1 C6 |
| B10 | **`rebuild_milvus` 清空向量但不重置 status/DocChunk** | `rebuild_milvus.py:24` | 数据一致性 | MEDIUM | §9.2 |
| B11 | **`_EXTRACTORS` 私有名被视图层直引** | `views.py:1365` | 解析统一入口 | LOW | §14.2 |
| B12 | **`retrieve_images` docstring 与代码不符** | `rag.py:1010` vs `:1042` | — | INFO | §12.5 |

---

## 19. Future Moderation Gate Matrix

### 19.1 消费行为门禁矩阵（任务书 §18 要求）

| 消费行为 | 当前入口 | 当前是否审核 | 当前风险 | 未来门禁 |
|----------|----------|-------------|----------|----------|
| **OSS 读取（parser）** | `extractor.py:251 extract_text_from_oss` | ❌ | **CRITICAL** | 仅在 moderation 流程内部调用；业务侧禁止直调 |
| **索引 indexing** | `tasks.py:111 index_document_task` | **部分**（仅前缀） | **CRITICAL** | `storage_state == promoted` AND `state ∈ INDEXABLE_MODERATION_STATES` |
| **Milvus 写入** | `tasks.py:225` / `seed_test_docs.py:916` | ❌ | **CRITICAL** | 同上；并**删除 seed 路径的直写能力** |
| **RAG 检索** | `rag.py:1093 retrieve` | ❌ | **CRITICAL** | 复用同一 `is_indexable_*` 判断（与索引门同源） |
| **LLM 消费（FAQ）** | `faq/tasks.py:287` | ❌ | **CRITICAL** | 同上 |
| **LLM 消费（freshness）** | `extractor.py:434` | ❌ | **CRITICAL** | 同上 |
| **citation** | `rag.py:1093` → `sse.py:267` | ❌ | **HIGH** | 跟随 RAG 门 |
| **图片检索** | `rag.py:999 retrieve_images` | ❌ | **HIGH** | 跟随 RAG 门 |
| **search / document query** | `rag.py:955`、`data_query.py:291` | 仅 `status` | **HIGH** | 跟随 RAG 门 |
| **下载** | `views.py:405`、`data_query.py:188` | ❌ | **HIGH** | `state ∈ INDEXABLE_...` AND `storage_state == promoted` AND visibility |
| **预览** | `views.py:1069` | ❌ | **HIGH** | 同下载 |
| **列表 / 详情元数据** | `views.py:89`、`:226` | ❌ | **HIGH** | visibility；且 **`oss_url` / `preview_url` 必须独立门禁** |
| **聊天引用详情** | `chat/views.py:178` | 仅 `status` | **HIGH** | 跟随 RAG 门 |
| **Admin 统计** | `dashboard.py:380`、`context.py:51` | ❌ | **INFO** | 无需门禁（仅计数） |
| **删除** | `views.py:285` | ❌ | **HIGH**（向量残留） | 删除时必须同步清 Milvus |

### 19.2 统一入口可行性（任务书 §19）

**建议的目标链路：**

```
                    ┌───────────────────────────────┐
   上传 / 爬虫 / 脚本 ─►│  quarantine/  (OSS)            │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  ContentModeration(state=scanning) │
                    │  + ModerationAttempt（历史）      │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  moderation service             │
                    │  ClamAV → Rule → LLM → Vision   │  ← P1-2C / P1-2D
                    │  统一调用 extractor             │
                    └───────────────┬───────────────┘
                          approved  │  rejected / unavailable
                                    ▼
                    ┌───────────────────────────────┐
                    │  promotion: quarantine → knowledge/│
                    │  storage_state = promoted        │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  index_document_task            │
                    │  ★ 唯一索引门（已就位）           │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  Milvus + DocChunk              │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │  RAG / FAQ / 下载 —— 复用同一判断 │
                    └───────────────────────────────┘
```

**结论：可以做到，但需要三个前置动作**

1. **收口写入**：`seed_test_docs.py` 必须停止自建流水线（B1）。
2. **收口读取**：把"是否可消费"抽成**一个共享判断函数**，让 RAG / FAQ / 下载 / 预览 全部调用同一个函数——**不要各写各的**（当前 RAG 的门写在 `authorized_knowledge_docs`，下载的门写在 `_admin_scope_filter`，二者语义已经不一致）。
3. **同步删除**：向量与 DocChunk 的生命周期必须与 `KnowledgeDoc` 对齐（B4）。

### 19.3 必须明确列出的旁路（任务书 §19 要求）

| 旁路形态 | 具体位置 |
|----------|----------|
| `KnowledgeDoc` → **直接 parser** | `extractor.py:414 extract_freshness_for_doc`、`views.py:1365`（私有名直引） |
| `KnowledgeDoc` → **直接 LLM** | `extractor.py:442`、`faq/tasks.py:288` |
| `KnowledgeDoc` → **直接 Milvus** | `seed_test_docs.py:916` |
| `KnowledgeDoc` → **直接 OSS** | `extractor.py:263`、`views.py:430/1132/1144`、`data_query.py:259`、`sse.py:267/321`、`serializers.py:54/62` |
| `KnowledgeDoc` → **直接 DocChunk** | `rag.py:505`、`rag.py:668`、`seed_test_docs.py:932` |

---

## 20. Recommended P1-2C Integration Points

**本阶段不实施任何一条。** 以下为建议，按优先级排列。

| # | 接入点 | 文件:行 | 内容 | 优先级 |
|---|--------|---------|------|--------|
| I1 | **索引门** | [moderation.py:151-160](backend/apps/knowledge/moderation.py#L151) | 把注释占位替换为真实判断：查 `ContentModeration` → `storage_state == promoted` AND `state ∈ INDEXABLE_MODERATION_STATES`；**`cm is None` 必须判为拒绝**（写法 A，见 §16.1）。9 条调用路径自动继承 | **P0** |
| I2 | **共享消费门** | 新增（建议放 `moderation.py`） | 抽出一个 `is_consumable(doc)`，供 RAG / FAQ / 下载 / 预览共同调用，**杜绝各写各的** | **P0** |
| I3 | **RAG 门** | [rag.py:808 `authorized_knowledge_docs`](backend/utils/rag.py#L808) | 唯一改动点即可覆盖 `retrieve` / `retrieve_images` / 4 条子通道 / `_filter_by_permission` / `_enrich_with_doc_meta` | **P0** |
| I4 | **FAQ 消费门** | [faq/tasks.py:265-269](backend/apps/faq/tasks.py#L265) | 在过滤条件中加 `storage_state` / `state` 约束（需 join `ContentModeration`） | **P0** |
| I5 | **freshness 门** | [extractor.py:424](backend/apps/knowledge/extractor.py#L424) | `KnowledgeDoc.objects.get(id=doc_id)` 之后立即加门；**或**在上传链路中把 `extract_freshness_task` 推迟到审核通过之后 | **P0** |
| I6 | **种子脚本收口** | [seed_test_docs.py:857](backend/scripts/seed_test_docs.py#L857) | 删除自建流水线，改为落地真实 OSS 对象 + 调用 `index_document_task`；或建立显式的可审计白名单通道 | **P0** |
| I7 | **下载 / 预览门** | [views.py:405](backend/apps/knowledge/views.py#L405)、[:1069](backend/apps/knowledge/views.py#L1069)、[data_query.py:188](backend/utils/data_query.py#L188) | 统一调用 I2 的 `is_consumable`；`find_user_docs` 需补 `status` 过滤 | **P1** |
| I8 | **序列化器 URL 字段门** | [serializers.py:54,62](backend/apps/knowledge/serializers.py#L54) | `oss_url` / `preview_url` 必须独立门禁，否则元数据接口是绕道下载入口 | **P1** |
| I9 | **删除同步清向量** | [views.py:310-316](backend/apps/knowledge/views.py#L310) | 补上 Milvus 删除（对应代码中的 TODO）；同时修 Chroma 降级下的空操作（[milvus.py:299](backend/utils/milvus.py#L299)） | **P1** |
| I10 | **修 `delete_by_doc_id` 调用** | [seed_test_docs.py:826](backend/scripts/seed_test_docs.py#L826) | 方法名应为 `delete_by_doc`；当前的 `try/except` 把 `AttributeError` 静默吞掉 | **P1** |
| I11 | **修 `filter_ids=[]` 语义** | [milvus.py:212-217](backend/utils/milvus.py#L212) | 空列表应表示"无候选"而非"不过滤" | **P0**（安全默认值） |
| I12 | **爬虫落地方式** | [crawler.py:449,479](backend/utils/crawler.py#L449) | 改为 `quarantine/` → 审核 → promotion，否则 P1-2D 门禁会打断爬虫 | **P1**（P1-2D 前置） |
| I13 | **解析器安全前置** | [extractor.py:182,209](backend/apps/knowledge/extractor.py#L182) | ClamAV 扫描必须在 `antiword` / `soffice` **之前** | **P1**（P1-2C） |
| I14 | **流式下载** | [extractor.py:277](backend/apps/knowledge/extractor.py#L277) | `resp.content` 全量读入内存，无上限；应改用 P1-2A 的 `iter_object_chunks(max_bytes=...)` | **P2** |
| I15 | **历史数据策略** | — | 见 §16.4；需要先决策 `legacy_unchecked` 是否新增状态 | **P1**（决策项） |
| I16 | **CAS 落地** | `applied_seq` | 模型已就位但无调用方；审核结果写回必须用 `WHERE id=? AND applied_seq<?` | **P1**（P1-2C 写入时） |

---

## 21. Risks

| # | 风险 | 等级 | 说明 |
|---|------|------|------|
| R1 | **审核模型零消费者** | **CRITICAL** | `ContentModeration` 建好但无人读写；系统安全状态与 P1-2A 之前**没有区别** |
| R2 | 索引门只校验前缀 | **CRITICAL** | `rejected` / `unavailable` / `quarantine` 全部放行（§6.5） |
| R3 | 上传后自动送 LLM | **CRITICAL** | 每条上传路径都会触发全文 LLM 调用（B2） |
| R4 | FAQ 消费未审核文档 | **CRITICAL** | `pending` / `rejected` / `quarantine` 均可进入 LLM（B3） |
| R5 | 种子脚本可写 Milvus | **CRITICAL** | 完整绕过链路（B1），且其 `oss_key` 前缀决定了它**必须**绕过 |
| R6 | Milvus 向量生命周期失控 | **HIGH** | 删除不删向量 / Chroma 空操作 / `delete_by_doc_id` 不存在（B4） |
| R7 | `find_user_docs` 无 status 过滤 | **HIGH** | 唯一连 `status` 都不看的读取路径 + 1 小时签名 URL（B5） |
| R8 | 下载/预览无审核门 | **HIGH** | quarantine 落地后会直接泄露隔离内容（B6、§13.2） |
| R9 | `filter_ids=[]` 静默全库检索 | **HIGH** | 潜在，当前被 `retrieve()` 的守卫挡住（B7） |
| R10 | 门禁语义已分裂 | **HIGH** | RAG 用 `authorized_knowledge_docs`（含 visibility），下载用 `_admin_scope_filter`（含 `NOT_PRIVATE_OR_OWN`），两套逻辑已经不同 |
| R11 | 存量文档全部无审核记录 | **HIGH** | 门禁一旦启用，默认语义决定是"全阻断"还是"全放行"，两者都危险（§16） |
| R12 | 爬虫不建 CM | MEDIUM | P1-2D 后会被门禁整体拒绝（§15.4） |
| R13 | `rebuild_milvus` 造成静默降级 | MEDIUM | Milvus 空但 `status=indexed`，RAG 静默退化为关键词检索（§9.2） |
| R14 | `seed_e2e` 伪造 indexed | MEDIUM | 污染 RAG 元数据（§5.1 C6） |
| R15 | 解析外部程序先于扫描 | MEDIUM | `antiword` / `soffice` 处理未扫描文件（§14.3） |
| R16 | `origin/main` 已前进 | MEDIUM | 本地基线与远端分叉，合并前需重新评估（§2） |

---

## 22. Deferred Items

以下**明确推迟**到后续阶段，本阶段只记录不动手：

| 阶段 | 事项 |
|------|------|
| **P1-2C** | I1–I6、I11、I13、I16（审核门 + 共享消费门 + 种子收口 + 解析前置 + CAS） |
| **P1-2D** | upload pipeline 改造（`upload → quarantine`）、promotion workflow、I7–I9、I12 |
| **P1-2E** | 历史数据策略（I15）、`seed_e2e` 清理、Celery 部署、parser 依赖、`rebuild_milvus` 一致性治理 |
| **不属本阶段** | ClamAV、YARA / Rule engine、LLM moderation、Vision moderation、OCR moderation、extraction service 重构、access gate、confirmation API、Celery retry、cleanup worker |
| **本阶段发现的其它缺陷（不顺手修）** | `rag.py:1010` docstring 过期、`views.py:1365` 私有名直引、`views.py:1264` 重复的可解析类型常量、`milvus.py:299` Chroma 空操作 |

---

## 23. Tests Executed

| # | 命令 | 结果 | 外部网络访问 | 是否修改工作树 |
|---|------|------|-------------|---------------|
| T1 | `manage.py check --settings=config.settings_test` | `System check identified no issues (0 silenced).` exit 0 | 无 | 否 |
| T2 | `manage.py test apps.knowledge --settings=config.settings_test -v 1` | `Ran 116 tests in 18.541s — OK`，exit 0 | **无**（`aliyuncs` 命中 **0**；`milvus` 命中 **0**） | 否 |

**未执行的测试及原因**：

- **全量测试套件**：未重复执行。理由：仓库中只有 `apps/accounts/tests.py` 与 `apps/knowledge/tests.py` 两个测试模块，**不存在 FAQ / chat / RAG 的测试**；`apps.accounts` 的 19 项失败是本轮之前既已确认的基线噪声，与本次审计对象无关。只跑 `apps.knowledge` 已覆盖全部与 KnowledgeDoc 索引相关的既有测试。
- **任何需要 Milvus / OSS / LLM / 生产 MySQL 的验证**：按 §21 要求**全部未执行**。

**测试环境隔离确认**：

- `config/settings_test.py` 将 `DATABASES.default` 覆盖为 `config.sqlite_test_backend` + `:memory:`，**不触碰生产 MySQL**。
- 知识库 OSS 测试全部 mock `oss_client._bucket`，日志中 `aliyuncs` 出现次数为 **0**。
- 测试后 `git status --short` 与测试前**逐字节一致**，工作树未被修改。

**本阶段对生产代码的修改：无。**

---

## 24. Final Verdict

审计覆盖完整：所有上传入口、全部索引路径、全部检索 / LLM 消费路径、全部 OSS 读取点、解析器、爬虫、删除路径均已定位到具体文件与行号；所有旁路均给出了可复现的证据。

```
========================================
P1-2B KNOWLEDGE CONSUMER AUDIT
==============================

Git HEAD:
174a64889643a09ebbb4e23d605ae4b8f131ac24

Origin Main:
001823baa7d3c25bf1deb985432c172c0f3a055f
（任务书给出 8c2d0e3e…，远端已前进，见 §2）

Branch:
phase-1-1-security-stopgap

Production Code Modified:
NO

Database Modified:
NO

OSS Modified:
NO

Push:
NO

Commit:
NO

Consumers Identified:
33

Critical Bypasses:
3
（B1 seed_test_docs 自建流水线 / B2 上传后自动送 LLM / B3 FAQ 消费未审核文档）

High Risks:
6
（B4 向量残留 / B5 find_user_docs / B6 下载预览无门 /
  B7 filter_ids=[] / R10 门禁语义分裂 / R11 存量无审核记录）

Moderation Gates Missing:
14
（§19.1 中除 Admin 统计外的全部消费行为）

Tests:
PASS
（manage.py check 通过；apps.knowledge 116 项全通过；
  aliyuncs=0，无外部网络访问，工作树未改动）

Final Verdict:
PASS
========================================
```

**`PASS` 的含义是"审计完成"，不是"系统已经安全"。** 恰恰相反——本次审计确认：**P1-2A 之后，系统在内容安全维度上与 P1-2A 之前没有区别**，因为新建立的审核模型至今没有任何消费者。安全状态的改善**全部尚未发生**。

---

## 25. 最重要的 10 条结论

1. **最大审核绕过点：`scripts/seed_test_docs.py:857 index_seed_docs`。** 它完整复制了"分块 → Embedding → 写 Milvus → 写 DocChunk → 置 `indexed`"的全套流水线，**不经过 `index_document_task`，也不经过 `check_indexable`**。更关键的是它**必须**绕过：它把 `oss_key` 写成 `seed/…`（[:849](backend/scripts/seed_test_docs.py#L849)），而 `check_indexable` 只认 `knowledge/` 前缀——走正规门只会被拒。**只要这个脚本还在，任何人都能用一条命令把任意内容直接写入生产 Milvus。**

2. **最大 RAG 绕过点：`utils/rag.py:1093 retrieve()` 没有任何审核门。** 它的全部门禁是 `authorized_knowledge_docs()`（**仅 visibility**）加上 `status='indexed'`。`status` 表达的是"处理进度"，**不是审核结论**——把安全语义挂在这个字段上是类别错误。四路召回（向量 / 关键词 / 关键词表 / 正文分块）**没有任何一路**检查 `moderation_state` 或 `storage_state`。

3. **最大 LLM 消费绕过点：`apps/knowledge/extractor.py:414 extract_freshness_for_doc`。** 它比 FAQ 那条更严重，因为它是**自动的、无条件的、发生在每一条上传路径上**：`views.py:785/1056/1499/1720` 与 `chunked_views.py:397` 都会 `safe_delay(extract_freshness_task, ...)`，而该任务只做 `KnowledgeDoc.objects.get(id=doc_id)` 就下载 OSS 全文送 LLM。**文档在 `moderation_state=pending` 时早已进入 LLM。** 其次是 `apps/faq/tasks.py:242`，它的筛选条件 `status__in=['uploaded','indexed']` 对 `pending` / `scanning` / `rejected` / `unavailable` / `quarantine` **全部放行**。

4. **必须在 P1-2C 统一收口的路径**：索引门（`moderation.py:151` 的一个接入点即可覆盖 9 条调用路径，这是成本最低的收口）；**读取侧必须抽出唯一共享判断函数**——现在 RAG 用 `authorized_knowledge_docs`、下载用 `_admin_scope_filter`，**两套逻辑已经不一致**，再加两套只会更糟；`extract_freshness` 与 FAQ 两处 LLM 消费；种子脚本；`find_user_docs`；序列化器里的 `oss_url` / `preview_url`。

5. **P1-2D 的 upload_pipeline 接入前提有三条硬约束**：(a) 爬虫现在直接写 `knowledge/` 且从不创建 `ContentModeration`（[crawler.py:449](backend/utils/crawler.py#L449)），**门禁一旦启用会把爬虫文档全部拒绝**，必须同步改造爬虫；(b) `extract_freshness_task` 必须推迟到审核通过之后，否则隔离区文件仍会被解析并送 LLM；(c) 删除文档必须同步清 Milvus——[views.py:302](backend/apps/knowledge/views.py#L302) 的代码注释明写这是个 TODO。

6. **存量数据是最大的未决策项。** migration 0008 不含数据迁移，因此**现有全部文档都没有 `ContentModeration`**。门禁启用时的写法决定后果：`cm is None → 拒绝` 会**一次性阻断全部存量内容**；`cm is None → 放行` 会**把全部历史文档默认当成已审核**——这正是 P1-2A 规范禁止的"制造虚假安全结论"。这个决策必须在 P1-2C 开始前拍板，不能留给实现者临场决定。

7. **`quarantine/` 目前是零使用命名空间，但读取侧对它毫无防备。** 全仓库没有任何代码读写 `quarantine/`；同时下载、预览、列表、详情、序列化器、`find_user_docs` **全部直接把 `doc.oss_key` 交给 `get_download_url` / `get_preview_url`，不校验前缀**。其中 `serializers.py:62` 的 `preview_url` 有效期是 **7 天**。若 P1-2D 只把对象挪进隔离区而不改这些接口，**隔离区内容可以通过签名 URL 直接拿到**。

8. **Milvus 向量的生命周期是全仓库最薄弱的一环。** 三个独立缺陷叠加：删除文档时**明确不删向量**（`views.py:302` TODO）；ChromaDB 降级模式下 `delete_by_doc` 是**空操作**（`milvus.py:299`）；`seed_test_docs.py:826` 调用的 `delete_by_doc_id` **这个方法根本不存在**，`AttributeError` 被 `except` 静默吞掉并记成"可忽略"。后果是"已 rejected 的文档仍保有向量""删除的文档仍保有向量"，且无人知晓。

9. **一个静默的默认值错误值得单独警惕**：`milvus.py:212` 的 `if filter_ids:` 使得**空列表等于"不过滤，检索整个 collection"**。当前被 `retrieve()` 的 `if not candidate_ids: return []` 挡住，但这是一个方向性错误的默认值——未来任何传空列表的调用方都会得到全库检索。加门时必须显式处理。

10. **可以安全进入下一阶段。** 本次审计未修改任何生产代码、未执行迁移、未写入任何数据库 / OSS / Milvus，工作树与 P1-2A 结束时逐字节一致，`manage.py check` 与 `apps.knowledge` 116 项测试全通过且外部网络访问为 0。P1-2C 的接入点已在 §20 明确定位到文件与行号，且**P1-2C 的成本主要集中在一个地方**——[moderation.py:151](backend/apps/knowledge/moderation.py#L151) 的那个接入点，因为 9 条索引路径都已经收敛到它。真正需要额外工程的是读取侧的统一门与存量数据策略。
