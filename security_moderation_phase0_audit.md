# 文件上传安全审核改造 — Phase 0 现状审计报告

| 项目 | 内容 |
|---|---|
| 审计对象 | 校园虚拟资源管理系统 后端（Django + DRF + 阿里云 OSS + Milvus + Celery） |
| 审计类型 | **只读** 安全架构审计（静态代码审计） |
| 审计阶段 | Phase 0：现状审计 |
| 审计日期 | 2026-09-10 |
| 代码基线 | `main` @ `b4c3774`（真实后缀） |
| 审计员 | Claude Code |

---

## 0. 路径更正声明（重要）

需求中给出的项目目录为：

```
D:\实验室204\校园虚拟资源管理\backend
```

**该路径在本机不存在**（`D:\` 下无 `实验室204` 目录，经 `ls /d/` 全量枚举确认）。本机实际存在的同名项目位于：

```
e:\github\campus_resource\backend
```

其目录结构、模块划分、`apps/` `utils/` `config/` 布局、OSS/Celery/Milvus 配置与需求描述完全一致（`manage.py`、`requirements.txt`、`apps/knowledge/`、`utils/oss.py`、`utils/sensitive_filter.py`、`config/celery.py` 均存在）。本报告基于 `e:\github\campus_resource\backend` 出具。

> 若该 D 盘路径属于另一台机器/另一份部署，请确认两者是否同源；本报告的结论**不可**直接套用于未经比对的另一份代码。

---

## 1. 审计结论

### 总体判定：`FAIL`

**当前系统不具备内容安全审核能力。核心判断如下：**

> **当前上传链路存在系统性的“文件未经任何安全审核即可进入正式存储、读取与索引”的问题。所有 10 个文件进入入口均无内容审核环节，所有文件直接写入正式业务前缀 `knowledge/`，所有上传路径在上传成功后立即触发索引任务，索引任务不校验任何审核状态即写入 Milvus 与 DocChunk。**

分项判定：

| 能力 | 判定 | 依据 |
|---|:--:|---|
| 文档内容敏感检测 | **FAIL** | `KnowledgeDoc.is_sensitive` / `sensitive_checked` 两个字段**从未被写入、从未被查询**（全仓库 grep 无任何赋值与过滤） |
| 病毒 / 恶意文件扫描 | **FAIL** | 无 ClamAV/任何 AV 依赖、无扫描调用（详见 §14） |
| 图片内容审核 | **FAIL** | 视觉模型仅用于生成标题/描述/关键词，**不是审核**，且失败的图片无任何兜底 |
| PDF / Office 文本审核 | **FAIL** | 提取出的文本仅截断 4000 字喂给分类提示词，**从不进入敏感检测** |
| 压缩包审核 | **FAIL** | 允许 zip/rar/7z 上传，但无解压、无扫描、无嵌套检查 |
| 隔离存储 | **FAIL** | 不存在 `quarantine/` 等任何隔离前缀；全部写入 `knowledge/` 正式前缀 |
| 审核状态机 | **FAIL** | `status` 枚举（uploaded/parsing/chunking/embedding/indexed/failed）表达的是**索引进度**，不是审核状态 |
| 统一 moderation service | **FAIL** | 不存在；`utils/sensitive_filter.py` 是**面向用户提问文本**的规则引擎，不覆盖上传 |
| 上传前拦截 | **FAIL** | 无任何入口具备“先审后存”或“先审后索引” |
| 违规内容可撤回 | **FAIL** | 签名 URL 有效期最长 **604800 秒（7 天）**，签发后不可撤销，且删除文档不回收已签发 URL |

**结论一句话**：当前系统的“安全审核”能力实为空壳——数据库里预留了两个字段（`is_sensitive`、`sensitive_checked`），但没有任何代码写入过它们；聊天侧对**用户提问**做了关键词拦截，而对**用户上传的文件内容**没有任何审核。

---

## 2. 当前架构（含实际文件/函数标注）

### 2.1 主链路

```
客户端
  │
  ├─(A) multipart 直传 ──► apps/knowledge/views.py::FileUploadView.post
  ├─(B) multipart 批量 ──► apps/knowledge/views.py::BatchFileUploadView.post
  ├─(C) JSON 文本录入 ──► apps/knowledge/views.py::TextUploadView.post
  ├─(D) STS 直传 ───────► apps/knowledge/views.py::UploadSTSView.post
  │                        └─► utils/oss.py::OSSClient.get_sts_credentials
  │                        └─► utils/oss.py::OSSClient._generate_oss_key
  └─(E) 回调建记录 ─────► apps/knowledge/views.py::UploadCallbackView.post
                            └─► _validate_oss_key()  ← 仅格式校验，不验存在/归属
  ▼
【OSS 写入】全部写入正式前缀  knowledge/{scope}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}
  ├─ put_object            utils/oss.py:313（upload_text）
  ├─ put_object            apps/knowledge/views.py:1201 / :1482
  ├─ init_multipart_upload apps/knowledge/chunked_views.py:162
  ├─ upload_part           apps/knowledge/chunked_views.py:219
  ├─ complete_multipart_upload apps/knowledge/chunked_views.py:278
  └─ sign_url('PUT')         utils/oss.py:144 / :168  ← 客户端直写
  ▼
【KnowledgeDoc 创建】 status='uploaded'
  ├─ apps/knowledge/views.py:715   (callback)
  ├─ apps/knowledge/views.py:893   (text)
  ├─ apps/knowledge/views.py:1303  (file)
  ├─ apps/knowledge/views.py:1519  (batch)
  ├─ apps/knowledge/chunked_views.py:318 (merge)
  └─ utils/crawler.py:463          (官网爬虫)
  ▼
【内容提取】 apps/knowledge/extractor.py::extract_text_from_oss
  └─ 下载 OSS → tempfile → _EXTRACTORS[ext](tmp_path)
     （注：FileUploadView 另有一处内联提取 apps/knowledge/views.py:1232-1253）
  ▼
【索引任务】 utils/celery_dispatcher.py::safe_delay(index_document_task, doc.id)
  └─ apps/knowledge/tasks.py::index_document_task
     ├─ 无任何审核状态校验 ← ★关键
     ├─ doc.status: uploaded → parsing → chunking → embedding → indexed
     ├─ utils/llm.py::llm_client.embed(texts)
     ├─ utils/milvus.py::milvus_client.insert_chunks()   → Milvus
     └─ DocChunk.objects.bulk_create()                   → MySQL
  ▼
【RAG 检索】 utils/rag.py
  └─ authorized_knowledge_docs(user_context).filter(status='indexed')  ← 唯一状态门
     （仅按 visibility/college/department 做权限，无任何审核条件）
  ▼
【聊天引用】 utils/sse.py::build_references_events
  └─ 返回 content_snippet(200字) + download_url(签名 300s)
  ▼
【读取】 apps/knowledge/views.py
  ├─ KnowledgeDocListView.get_queryset      → _admin_scope_filter
  ├─ KnowledgeDocDetailView                 → download_url/oss_url 签名 604800s
  ├─ KnowledgeDocDownloadView               → 签名 300s
  └─ KnowledgeDocPreviewView                → 文本类直接回传正文
```

### 2.2 旁路链路（不在 `apps/knowledge/views.py` 内，容易漏审）

```
utils/crawler.py::crawl_and_index
  ├─ 调用方 1：apps/knowledge/tasks.py::crawl_neusoft_website_task（beat 每天 04:00 / 每周日 05:00）
  ├─ 调用方 2：apps/knowledge/management/commands/crawl_website.py
  └─ 调用方 3：apps/faq/tasks.py:267 内部先 crawl_and_index 再消费文档
       └─ 同样：upload_text → OSS knowledge/website/0/... → KnowledgeDoc(status='uploaded')
          → safe_delay(index_document_task)   ← 无审核

apps/chat/views.py::VoiceAskView  (POST /api/chat/voice-ask/)
  └─ request.FILES['audio_file'] → 内存 → utils/asr.py
     不落 OSS、不建 KnowledgeDoc，转写文本会过 check_sensitive（覆盖，但仅此一处）

scripts/*.py  （开发/运维脚本，可写库写 OSS）
  ├─ scripts/seed_test_docs.py:835   KnowledgeDoc.objects.create + 索引
  ├─ scripts/index_all_docs.py:105   index_document_task.run() 批量索引 status='uploaded'
  ├─ scripts/index_quick.py:25       同上（前 3 篇）
  └─ scripts/fix_oss_content_type.py:88  bucket.copy_object()  ← 仓库中唯一 copy_object 调用
```

---

## 3. 全部上传入口清单

> “输入类型”指真正进入系统的载荷。**所有入口的“是否经过敏感检测”均为「否」**——除非特别注明。

| # | 入口 | 文件 | 函数/类 | HTTP 接口 | 输入类型 | 是否写 OSS | 是否创建 KnowledgeDoc | 是否触发索引 | 经过敏感检测 |
|:-:|---|---|---|---|---|---|:-:|:-:|:-:|
| 1 | 文件直传 | [views.py](backend/apps/knowledge/views.py) | `FileUploadView.post` L1135 | `POST /api/knowledge/upload/file/` | multipart 文件 | ✅ L1201 | ✅ L1303 | ✅ L1354 | ❌ |
| 2 | 批量上传 | [views.py](backend/apps/knowledge/views.py) | `BatchFileUploadView.post` L1400 | `POST /api/knowledge/upload/batch/` | multipart 多文件 | ✅ L1482 | ✅ L1519 | ✅ L1568 | ❌ |
| 3 | 文本录入 | [views.py](backend/apps/knowledge/views.py) | `TextUploadView.post` L800 | `POST /api/knowledge/upload/text/` | JSON 纯文本 | ✅ L871 | ✅ L893 | ✅ L926 | ❌ |
| 4 | STS 取凭证 | [views.py](backend/apps/knowledge/views.py) | `UploadSTSView.post` L613 | `POST /api/knowledge/upload/sts/` | 文件名+大小+MD5 | ✅ 签名 PUT URL | ❌ | ❌ | ❌ |
| 5 | 上传回调 | [views.py](backend/apps/knowledge/views.py) | `UploadCallbackView.post` L680 | `POST /api/knowledge/upload/callback/` | oss_key + 自报元数据 | ❌ | ✅ L715 | ✅ L752 | ❌ |
| 6 | 分片初始化 | [chunked_views.py](backend/apps/knowledge/chunked_views.py) | `ChunkUploadInitView.post` L106 | `POST /api/knowledge/upload/chunk/init/` | md5+filename+size | ✅ init_multipart L162 | ❌ | ❌ | ❌ |
| 7 | 分片接收 | [chunked_views.py](backend/apps/knowledge/chunked_views.py) | `ChunkUploadView.post` L195 | `POST /api/knowledge/upload/chunk/` | multipart 分片流 | ✅ upload_part L219 | ❌ | ❌ | ❌ |
| 8 | 分片合并 | [chunked_views.py](backend/apps/knowledge/chunked_views.py) | `ChunkMergeView.post` L245 | `POST /api/knowledge/upload/chunk/merge/` | upload_id + 元数据 | ✅ complete L278 | ✅ L318 | ✅ L342 | ❌ |
| 9 | AI 分类（2 个别名） | [views.py](backend/apps/knowledge/views.py) | `AIClassifyView.post` L1789 | `POST /api/knowledge/upload/ai-classify/`<br>`POST /api/knowledge/upload/classify/` | JSON 文本 / base64 图片 | ❌ | ❌ | ❌ | ❌ |
| 10 | 语音问答 | [chat/views.py](backend/apps/chat/views.py) | `VoiceAskView.post` L894 | `POST /api/chat/voice-ask/` | multipart 音频 ≤10MB | ❌ 仅内存 | ❌ | ❌ | ✅ L991（转写文本过 `check_sensitive`） |
| 11 | 官网爬虫 | [crawler.py](backend/utils/crawler.py) | `crawl_and_index` → `save_doc` L463 | Celery beat / 管理命令 / FAQ 任务 | 网页正文（外部） | ✅ L451 | ✅ L463 | ✅ L491 | ❌ |
| 12 | 开发脚本 | [seed_test_docs.py](backend/scripts/seed_test_docs.py) | `main` L835 | 无（CLI） | 硬编码文本 | ✅ | ✅ | ✅ | ❌ |
| 13 | 运维脚本 | [fix_oss_content_type.py](backend/scripts/fix_oss_content_type.py) | `main` L88 | 无（CLI） | 已有 OSS 对象 | ✅ copy_object | ❌ | ❌ | ❌ |
| 14 | Django Admin | — | — | `/admin/` | — | ❌ | ❌ | ❌ | N/A |

**Django Admin 确认**：[apps/knowledge/admin.py](backend/apps/knowledge/admin.py) 全文仅 `from django.contrib import admin`，**未注册任何模型**。`KnowledgeDoc` / `DocChunk` / `ChunkedUploadSession` 均不在 admin 中注册，因此 Django Admin **不构成上传入口**。已注册的模型为 `User`/`College`/`Conversation`/`ChatMessage`/`QueryLog`/`UploadLog`/`Discipline`/`Category`/`FAQ*`（见 `apps/*/admin.py`），均不含文件上传字段。

**权限门槛（已逐行核对）**：

| 入口 | permission_classes | 行号 |
|---|---|---|
| STS / Callback / Text / File / Batch / AIClassify | `[IsAuthenticated, IsAdminOrSuperAdmin]` | [views.py:611](backend/apps/knowledge/views.py#L611) / [:678](backend/apps/knowledge/views.py#L678) / [:797](backend/apps/knowledge/views.py#L797) / [:1127](backend/apps/knowledge/views.py#L1127) / [:1394](backend/apps/knowledge/views.py#L1394) / [:1787](backend/apps/knowledge/views.py#L1787) |
| 分片 init / chunk / merge | `[IsAuthenticated, IsAdminOrSuperAdmin]` | [chunked_views.py:103](backend/apps/knowledge/chunked_views.py#L103) / [:192](backend/apps/knowledge/chunked_views.py#L192) / [:242](backend/apps/knowledge/chunked_views.py#L242) |
| 文档列表 / 详情 | **`[IsAuthenticated]`** | [views.py:112](backend/apps/knowledge/views.py#L112) / [:248](backend/apps/knowledge/views.py#L248) |
| 下载 / 预览 / 提取日期 | `[IsLoginUser]` / `[IsAuthenticated]` | [views.py:403](backend/apps/knowledge/views.py#L403) / [:955](backend/apps/knowledge/views.py#L955) / [:1065](backend/apps/knowledge/views.py#L1065) |

因此：**上传类入口（1–8）均要求 `super_admin / admin / college_admin / dept_admin`，普通 `user` 不可上传**；但**读取类入口（列表 / 详情 / 预览）只需任意登录用户**，其访问范围完全依赖 `_admin_scope_filter`（见 R26）。入口 10（语音）仅需登录。入口 11 由 Celery 定时任务以 `uploader_id=1`（超管）身份执行。

> 附注：[config/permissions.py:66](backend/config/permissions.py#L66) 定义了 `CanUploadDocument`（「任何已认证用户均可上传」），但**未被任何上传视图使用**——若 Phase 1 有把它接到上传入口的计划，需注意这会比现状**放宽**权限。

---

## 4. OSS 写入点清单

正式路径规则（三处**重复实现**，逻辑一致）：

```python
# utils/oss.py:122   _generate_oss_key
# apps/knowledge/views.py:1186 / :1467 / :868   （file / batch / text）
# apps/knowledge/chunked_views.py:53            _build_oss_key
knowledge/{scope}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}
# scope ∈ {school: owner=0, department: owner=department_id, college: owner=college_id or 0}
```

| # | 写入点 | 文件:行 | 调用方 | 目标前缀 |
|:-:|---|---|---|---|
| W1 | `bucket.put_object` | [views.py:1201](backend/apps/knowledge/views.py#L1201) | `FileUploadView` | `knowledge/{scope}/…` |
| W2 | `bucket.put_object` | [views.py:1482](backend/apps/knowledge/views.py#L1482) | `BatchFileUploadView` | `knowledge/{scope}/…` |
| W3 | `bucket.put_object` | [oss.py:313](backend/utils/oss.py#L313) ← `upload_text` | `TextUploadView`（views.py:871） | `knowledge/{scope}/…` |
| W4 | `bucket.put_object` | [oss.py:313](backend/utils/oss.py#L313) ← `upload_text` | `utils/crawler.py:451` | `knowledge/website/0/…` |
| W5 | `bucket.init_multipart_upload` | [chunked_views.py:162](backend/apps/knowledge/chunked_views.py#L162) | `ChunkUploadInitView` | `knowledge/{scope}/…` |
| W6 | `bucket.upload_part` | [chunked_views.py:219](backend/apps/knowledge/chunked_views.py#L219) | `ChunkUploadView` | 同上（分片） |
| W7 | `bucket.complete_multipart_upload` | [chunked_views.py:278](backend/apps/knowledge/chunked_views.py#L278) | `ChunkMergeView` | 同上（合并即正式对象） |
| W8 | `bucket.sign_url('PUT', …)` | [oss.py:144](backend/utils/oss.py#L144)（dev）/ [:168](backend/utils/oss.py#L168)（prod） | `UploadSTSView` | **客户端直写** `knowledge/{scope}/…` |
| W9 | `bucket.copy_object` | [scripts/fix_oss_content_type.py:88](backend/scripts/fix_oss_content_type.py#L88) | 运维脚本（仅改 Content-Type） | 原地自拷贝 |

### 4.1 删除点（影响“撤回”能力）

| 删除点 | 文件:行 | 说明 |
|---|---|---|
| `oss_client.delete` | [views.py:298](backend/apps/knowledge/views.py#L298) | 单文档删除 |
| `oss_client.delete` | [views.py:366](backend/apps/knowledge/views.py#L366) | 批量删除 |
| `oss_client.delete` | [chunked_views.py:294](backend/apps/knowledge/chunked_views.py#L294) | merge 命中秒传时删除刚合并的对象 |
| `bucket.delete_object` | [oss.py:331](backend/utils/oss.py#L331) | 底层封装 |
| `bucket.abort_multipart_upload` | [tasks.py:295](backend/apps/knowledge/tasks.py#L295) | 清理超时会话 |

**注意**：删除文档**不会**使已签发但未过期的签名 URL 失效（见 §13 R7）。

### 4.2 回答 §5 的三个必答问题

**Q1：当前正式 OSS 路径是什么？**

```
knowledge/{school|college|department}/{owner_id}/{YYYY-MM}/{随机12位hex}.{ext}
外加爬虫专用：knowledge/website/0/{YYYY-MM}/crawl_{随机12位hex}.txt
```

**Q2：是否已存在隔离目录？**

> **当前不存在独立隔离存储。**
>
> 全仓库未出现 `quarantine` / `pending` / `unchecked` / `tmp-upload` 等任何隔离前缀。所有写入点（W1–W8）**直接落在 `knowledge/` 正式前缀**，客户端一旦拿到签名 PUT URL（W8）即可写入正式前缀，服务端不具备任何“先落隔离区再转正”的机制。

**Q3：客户端是否可以直接写 OSS？**

**是，通过 STS 入口（入口 4）**。详细回答见 §10。

**Q4：是否存在 OSS copy/move 操作？**

> **`utils/oss.py` 的 OSS 封装不支持 copy / move。**
>
> `OSSClient` 仅有：`get_sts_credentials` / `get_download_url` / `get_preview_url` / `compute_md5` / `check_duplicate` / `upload_text` / `delete`（见 [utils/oss.py:72-335](backend/utils/oss.py#L72)）。
> 全仓库唯一的 `copy_object` 出现在运维脚本 [scripts/fix_oss_content_type.py:88](backend/scripts/fix_oss_content_type.py#L88)，且用于原地改写 Content-Type，未封装进 `OSSClient`。
>
> **对 Phase 1 的直接影响**：`quarantine → promoted` 的转正动作**需要对 `utils/oss.py` 新增 copy/move 能力，当前基础设施不存在**。若采用“拷贝转正”，还需同步迁移 `KnowledgeDoc.oss_key`，而该字段在 `KnowledgeDocSerializer.read_only_fields` 中（[serializers.py:39](backend/apps/knowledge/serializers.py#L39)），需评估写入路径。

---

## 5. KnowledgeDoc 生命周期

### 5.1 实际状态枚举（**这是索引状态，不是审核状态**）

[models.py:7-14](backend/apps/knowledge/models.py#L7)

| 值 | 含义 | 由谁写入 |
|---|---|---|
| `uploaded` | 已上传（初始值） | 6 个创建点 |
| `parsing` | 解析中 | `tasks.py:151` |
| `chunking` | 分块中 | `tasks.py:171` |
| `embedding` | 向量化中 | `tasks.py:184` |
| `indexed` | 已索引 | `tasks.py:230` |
| `failed` | 失败 | `tasks.py:164,177,251` |

**不存在** `pending_moderation` / `reviewing` / `rejected` / `quarantined` 等审核态。

### 5.2 实际生命周期（按代码真实行为）

```
上传请求
  │
  ├─ [同步] put_object / upload_text / complete_multipart   → 文件落 OSS 正式前缀
  │        ★此时文件已可被签名 URL 访问
  │
  ├─ [同步] KnowledgeDoc.objects.create(status='uploaded')
  │        ★无条件创建，无审核判定，无审批人，无待审标记
  │
  ├─ [同步] Keyword.objects.get_or_create / bulk             （元数据，无审核）
  │
  └─ [异步] safe_delay(index_document_task, doc.id)
           │
           ├─ KnowledgeDoc.objects.get(id=doc_id)            ← 仅校验存在
           │  ★无 status / 审核 / 存储状态 校验
           ├─ status='parsing' → status='chunking' → status='embedding'
           ├─ Milvus insert_chunks  +  DocChunk.bulk_create
           │  ★此时内容已进入 RAG 可检索集合
           └─ status='indexed'
              ★此时内容对所有符合 visibility/college 授权的用户可见
```

### 5.3 关键结论

1. **创建 KnowledgeDoc 时，文件已经进入正式 OSS**（先写 OSS，后建记录）——不存在“审核通过才落正式存储”的前提。
2. **创建 KnowledgeDoc 时立即触发索引**，`status='uploaded'` 只是中间态，没有任何分支在 `uploaded` 停留等待审核。
3. **六个创建点中，没有任何一处设置 `is_sensitive` 或 `sensitive_checked`**（两者由 `default=False` 兜底）。
4. **状态机的唯一分支是“成功/失败”**，没有“可疑/拒绝/隔离”分支。

### 5.4 状态可被客户端篡改（★ CRITICAL 级设计缺陷）

[KnowledgeDocSerializer](backend/apps/knowledge/serializers.py#L23-L43) 的 `fields` 包含 `status`、`is_sensitive`、`sensitive_checked`、`visibility`，而 `read_only_fields`（L38-43）**未包含它们**：

```python
read_only_fields = [
    'id', 'source_type', 'oss_key', 'file_name', 'file_type',
    'file_size', 'md5_hash', 'page_count', 'uploader',
    'chunk_count', 'created_at', 'updated_at', 'indexed_at',
    'index_error', 'index_attempts', 'index_started_at', 'failed_at',
]
```

结合 [KnowledgeDocDetailView](backend/apps/knowledge/views.py#L220)（`RetrieveUpdateAPIView`，`perform_update` L257 仅校验 `super_admin` 或 `uploader_id == request.user.id`），**任何文档上传者可对自己上传的文档执行**：

```http
PUT /api/knowledge/docs/{id}/
{"status": "indexed", "is_sensitive": false, "sensitive_checked": true}
```

→ 后果：
- `status='indexed'` 使文档通过 RAG 的 `filter(status='indexed')` 状态门（内容侧需 Milvus 有向量才真正召回，见 §9）；
- `is_sensitive` / `sensitive_checked` 可被**写入**——若 Phase 1 将审核门控建立在这两个字段之上，该门控**可被文档所有者一行 PUT 绕过**；
- `visibility` 可被改为 `public`，使原本私密文档进入 RAG 检索范围（`authorized_knowledge_docs` 过滤 `visibility='public'`）。

> **设计红线**：Phase 1 的审核状态字段**必须**加入 `read_only_fields`，或改用独立的、客户端不可写的审核模型。

---

## 6. 当前安全检查能力

### 6.1 已有能力

| 能力 | 位置 | 说明 |
|---|---|---|
| 用户**提问**敏感词拦截 | [utils/sensitive_filter.py](backend/utils/sensitive_filter.py) `check_sensitive()` | 4 路静态规则引擎：涉密关键词 / 个人隐私正则 / 结构化保密数据 / 保密特殊逻辑。命中即拦截 |
| 提问拦截调用点 | [chat/views.py:336](backend/apps/chat/views.py#L336)（`AskView`）、[chat/views.py:991](backend/apps/chat/views.py#L991)（`VoiceAskView`） | 覆盖文本 + 语音两条提问通道 |
| 拦截留痕 | [chat/models.py:98](backend/apps/chat/models.py#L98) `SensitiveLog` | 记录 user/question/matched_rules/matched_texts/refuse_message |
| 拦截后台可见 | [admin_panel/views.py:609](backend/apps/admin_panel/views.py#L609) | 超管可查 SensitiveLog |
| 文件类型白名单 | [serializers.py:126](backend/apps/knowledge/serializers.py#L126) `ALLOWED_FILE_TYPES` | 42 种扩展名 |
| 文件大小限制 | [serializers.py:95](backend/apps/knowledge/serializers.py#L95) `FILE_SIZE_LIMITS` | **但分片上传未应用，见 R6** |
| 扩展名→Content-Type 映射 | [utils/oss.py:27](backend/utils/oss.py#L27) `MIME_MAP` | 防 MIME 混淆（浏览器内联风险） |
| `oss_key` 格式校验 | [views.py:767](backend/apps/knowledge/views.py#L767) `_validate_oss_key` | 仅禁 `..`、禁前导 `/`、要求 `knowledge/` 前缀 |
| 文档级可见性 | `visibility` + `_admin_scope_filter` / `authorized_knowledge_docs` | **权限控制，非内容审核** |

### 6.2 缺失能力（全部缺失）

| 能力 | 状态 | 证据 |
|---|:--:|---|
| 文档内容敏感检测 | **缺失** | `exp 1-11` 全部入口无调用 `check_sensitive` |
| 病毒/恶意文件扫描 | **缺失** | 无 ClamAV/AV 依赖与调用 |
| 图片内容审核 | **缺失** | 视觉模型仅生成标题/描述/关键词（[views.py:1704-1725](backend/apps/knowledge/views.py#L1704)），非审核；无 `nsfw` / `moderation` 任何字样 |
| PDF 文本审核 | **缺失** | 提取文本仅 `[:4000]` 喂分类提示词 |
| Office 文本审核 | **缺失** | 同上（`DOCUMENT_EXTRACTABLE_TYPES`） |
| 压缩包检查 | **缺失** | zip/rar/7z 在白名单内，但 `extractor._EXTRACTORS` 无解压分支，既不扫描也不解压 |
| 隔离存储 | **缺失** | 无 `quarantine/` 前缀 |
| 审核状态机 | **缺失** | 无审核相关枚举值 |
| 统一 moderation service | **缺失** | 不存在该模块 |
| 审核失败处理策略 | **缺失** | 无“审核失败”路径，因此无处理 |
| 人工复审队列 | **缺失** | 无待审队列模型 |
| 上传限流 | **缺失** | `REST_FRAMEWORK` 无 `DEFAULT_THROTTLE_CLASSES`（[settings.py:134-149](backend/config/settings.py#L134)） |

### 6.3 关于 `is_sensitive` / `sensitive_checked` 承担“审核状态”职责的确认

**确认：当前这两个字段不承担、也无法承担内容安全审核状态职责。它们是死字段。**

证据（全仓库 grep，排除 migrations）：

```
$ grep -rn "is_sensitive\s*=\|sensitive_checked\s*=" --include="*.py" .
apps/knowledge/models.py:127   is_sensitive = models.BooleanField(default=False, ...)      ← 字段定义
apps/knowledge/models.py:128   sensitive_checked = models.BooleanField(default=False, ...) ← 字段定义
utils/sensitive_filter.py:220,232,244,255,265,275  result.is_sensitive = True            ← 另一个类的属性，与 KnowledgeDoc 无关
```

```
$ grep -rn "filter(.*sensitive|exclude(.*sensitive" --include="*.py" .
（无输出）
```

即：
- **没有任何代码写入过 `KnowledgeDoc.is_sensitive` / `sensitive_checked`**（两个 `BooleanField(default=False)` 恒为 `False`）；
- **没有任何查询以它们为条件**；
- 它们只在 [KnowledgeDocSerializer.fields:34](backend/apps/knowledge/serializers.py#L34) 与接口文档字符串（[views.py:243](backend/apps/knowledge/views.py#L243)）中被“展示”，形成“系统有敏感审核”的假象。

### 6.4 覆盖范围差异

`utils/sensitive_filter.py` 的设计目标（其 docstring 自述）是：

> 定位：拦截 AI 平台自带过滤之外的场景特有敏感内容 / 这里负责：涉密保密 + 个人隐私 + 结构化数据 + 保密边界行为

其入参注释明确写为 **`text: 用户提问文本`**（[sensitive_filter.py:206](backend/utils/sensitive_filter.py#L206)）。**该模块从设计上就不是文件内容审核器**，Phase 1 不应复用其规则集作为文档审核唯一依据（可复用为审核流水线中的一路规则）。

---

## 7. 内容提取审计

审计对象：[apps/knowledge/extractor.py](backend/apps/knowledge/extractor.py)（另有一处内联提取：[views.py:1232-1253](backend/apps/knowledge/views.py#L1232)）

| 类型 | 当前提取方式 | 是否执行脚本 | 是否执行宏 | 是否访问外部资源 | 是否有限制 |
|---|---|:-:|:-:|:-:|---|
| `txt` | `open(...,errors='ignore').read()` [L233](backend/apps/knowledge/extractor.py#L233) | ❌ | ❌ | ❌ | ❌ 无长度限制（全量读入内存） |
| `md` | 同 txt [L234](backend/apps/knowledge/extractor.py#L234) | ❌ | ❌ | ❌ | ❌ 同上 |
| `html`/`htm` | 按纯文本读，**不解析 DOM** [L235-236](backend/apps/knowledge/extractor.py#L235) | ❌ | ❌ | ❌ | ❌ 不执行 `<script>`，但原始 HTML（含脚本/外部引用）作为纯文本入库 |
| `csv` | `csv.reader`，UTF-8/GBK 回退 [L134](backend/apps/knowledge/extractor.py#L134) | ❌ | ❌ | ❌ | ❌ 无行数限制 |
| `pdf` | PyMuPDF `fitz`，失败回退 pdfplumber [L49-73](backend/apps/knowledge/extractor.py#L49) | ❌ | ❌ | ❌ | ❌ 代码注释明确写「移除页数限制，读取全部内容」(L65) |
| `doc` | **`subprocess.run(['antiword', path], timeout=30)`** [L182](backend/apps/knowledge/extractor.py#L182) → docx2txt → **`subprocess.run(['soffice','--headless','--convert-to','txt',…], timeout=60)`** [L208](backend/apps/knowledge/extractor.py#L208) | ✅ **antiword / LibreOffice** | ❌ | ❌ | ⚠️ 有 30s/60s 超时；**无沙箱、无资源限制、无网络隔离** |
| `docx` | `python-docx` 段落+表格 [L76](backend/apps/knowledge/extractor.py#L76) | ❌ | ❌ | ⚠️ 外部关系（图片/超链）未解析 | ❌ 无大小/嵌套限制 |
| `xls` | `xlrd` [L152](backend/apps/knowledge/extractor.py#L152) | ❌ | ❌ | ❌ | ❌ 无行列限制 |
| `xlsx` | `openpyxl`，**全部 sheet 全部行** [L114](backend/apps/knowledge/extractor.py#L114) | ❌ | ❌ | ❌ | ❌ 无行列限制 |
| `ppt`/`pptx` | `python-pptx` 全幻灯片 [L98](backend/apps/knowledge/extractor.py#L98) | ❌ | ❌ | ❌ | ❌ 无限制 |
| 图片 | **无提取器**（png/jpg/… 不在 `_EXTRACTORS`） | ❌ | ❌ | ❌ | 兜底走 L281-284：以文本方式打开二进制 → 明文乱码入库 |
| 压缩包 | **无提取器**（zip/rar/7z 不在 `_EXTRACTORS`） | ❌ | ❌ | ❌ | 同图片，作为二进制文本兜底读取 |
| 其他 | 同压缩包 | ❌ | ❌ | ❌ | — |

### 逐项风险确认

| 检查项 | 结论 |
|---|---|
| **PDF 页数限制** | ❌ **风险：未发现对应限制**。`extractor.py:65` 注释「移除页数限制，读取全部内容」；`_parse_pdf` 直接遍历 `doc` 全部页 |
| **文本长度限制** | ❌ **风险：未发现对应限制**。`extract_text_from_oss` 全量返回；仅 `extract_freshness_with_llm` 内部 `TEXT_SNIPPET_CHARS=6000` 截断**喂给 LLM 的那一份**，不影响落库 |
| **压缩包嵌套** | ⚠️ **不适用/未确认**：系统不解压任何压缩包，故不存在嵌套解压；但压缩包内容**完全未经检查**即入库并可下载 |
| **解压总大小** | ⚠️ 同上，不解压（反而规避了 zip bomb 的解压侧风险，但留下“未审即可下载”缺口） |
| **单文件大小** | ⚠️ **部分**：`FILE_SIZE_LIMITS` 在 file/batch/text/callback/sts 生效；**分片上传未生效**（见 R6） |
| **处理超时** | ⚠️ **仅 `doc` 类型的 subprocess 有 30s/60s 超时**；PDF/DOCX/PPTX/XLSX 解析**无超时**，超大文件可长时间占用 Celery worker |
| **zip bomb** | ❌ **风险：未发现对应限制**（且无解压，故不触发；但 `openpyxl` / `python-pptx` 内部均支持 ZIP 解析，存在 XML bomb / 超大 sheet 风险） |
| **Office 宏** | ✅ **不执行宏**。`python-docx`/`python-pptx`/`openpyxl` 不执行 VBA；DOC 走 antiword/soffice 转换，`--headless --convert-to txt` 不执行宏。**但宏仍随原文件保存并可被下载，终端用户打开时可能执行** |
| **HTML 脚本** | ⚠️ **不执行**（按纯文本读取），但原始 HTML 以 `text/html` Content-Type 存 OSS（[oss.py:49](backend/utils/oss.py#L49)），**浏览器直接打开签名 URL 时会执行其中的脚本** → 存储型 XSS 风险（OSS 域，非主站域） |
| **外部链接/引用** | ❌ **风险：未发现对应限制**。DOCX/PPTX 的外部关系（远程图片、OLE 链接）未被剥离；文档正文中的 URL 会被原样提取并入库 |
| **路径穿越** | ✅ 提取阶段安全：`extract_text_from_oss` 用 `tempfile.mkstemp` 生成随机临时文件（[L271](backend/apps/knowledge/extractor.py#L271)），文件名不参与路径拼接。**注意：`config/celery.py:112` 存在真实的路径穿越，见 R11** |
| **临时文件清理** | ✅ `finally` 中 `os.remove`（[L289-293](backend/apps/knowledge/extractor.py#L289)） |

---

## 8. 索引路径与 Celery 审计

### 8.1 索引链路

```
上传成功
  ↓  utils/celery_dispatcher.py::safe_delay(index_document_task, doc.id)
  ├─ 优先 task.delay() → Celery + Redis 异步
  └─ 异常（含 Redis 不可用 / celery 未安装）→ 降级同步执行 task(*args)
  ↓
apps/knowledge/tasks.py::index_document_task(self, doc_id)
  ├─ KnowledgeDoc.objects.get(id=doc_id)        ← 仅校验存在
  ├─ status='parsing'
  ├─ extract_text_from_oss(oss_key, file_type, file_name)
  ├─ status='chunking' → chunk_document(text, 500, 100)
  ├─ status='embedding' → llm_client.embed(texts)
  ├─ milvus_client.delete_by_doc(doc_id); DocChunk.objects.filter(doc_id).delete()
  ├─ milvus_client.insert_chunks(milvus_chunks, embeddings)      → Milvus
  ├─ DocChunk.objects.bulk_create(doc_chunks)                    → MySQL
  └─ status='indexed'
```

### 8.2 Celery 任务入口是否重新验证？

| 校验项 | 是否执行 | 证据 |
|---|:-:|---|
| `KnowledgeDoc` 存在？ | ✅ | [tasks.py:138-141](backend/apps/knowledge/tasks.py#L138) |
| **storage 状态？** | ❌ | 无任何 `head_object` / 存在性校验（全仓库仅 [scripts/fix_oss_content_type.py:65](backend/scripts/fix_oss_content_type.py#L65) 用 `head_object`） |
| **审核状态？** | ❌ | 无 `is_sensitive` / `sensitive_checked` / moderation 字段校验 |
| **文件是否真的可用？** | ⚠️ 隐式 | 仅当 `extract_text_from_oss` 返回空时报 `failed`，不做预校验 |

**结论：`index_document_task` 是一个纯粹的“按 id 索引”任务，不具备任何安全闸门，任何能触发它的路径都会导致索引。**

### 8.3 `safe_delay` 同步降级 —— 高优先级安全问题确认

[utils/celery_dispatcher.py:15-49](backend/utils/celery_dispatcher.py#L15)：

```python
def safe_delay(task, *args, **kwargs):
    try:
        return task.delay(*args, **kwargs)
    except Exception as e:
        logger.warning('Celery 异步分发失败 (%s: %s)，降级为同步执行 task=%s', ...)
        try:
            return task(*args, **kwargs)     # ← 同步执行
        except Exception as sync_e:
            logger.error(...)
            return None
```

叠加 [utils/shared_task_compat.py:42-86](backend/utils/shared_task_compat.py#L42)：celery 未安装时 `@shared_task` 直接变成同步执行器。

**必须明确指出（需求方关注的高优先级问题）：**

> **是。Celery 不可用时的同步 fallback，会使得“审核尚未完成的文件被立即索引”成为必然结果，而非可能结果。**
>
> 因为在当前实现中，`safe_delay(index_document_task, …)` 与 `safe_delay` 的**同步降级是同一行代码**：上传视图在创建 `KnowledgeDoc` 后立即调用它。当 Redis/Celery 不可用时，索引在**同一个 HTTP 请求线程内同步完成**，用户在收到 201 响应时 `status` 已经是 `indexed`，文件已进入 Milvus。
>
> 这意味着：**在 Phase 1 引入审核后，若不同时修改 `safe_delay` 的调用点或 `index_document_task` 的入口校验，生产环境 Redis 抖动、或本地开发环境（requirements 未装 celery 时）将直接绕过审核闸门。审核校验必须做在 `index_document_task` **任务体内**（而非调用方），并对同步降级路径生效——因为 `shared_task_compat` 的降级同样会直达任务体，两者共用同一入口，这是唯一可靠的插桩点。**
>
> 补充：`ExtractFreshnessView` 还**显式**提供了 `?async=false` 参数走同步执行（[views.py:1080-1094](backend/apps/knowledge/views.py#L1080)），说明“同步执行 = 正常可用路径”在本项目中是被接受的运维形态，不能依赖“生产一定异步”来兜底。

### 8.4 索引触发点全清单

| # | 触发点 | 文件:行 | 上下文 |
|:-:|---|---|---|
| I1 | `safe_delay(index_document_task, …)` | [views.py:752](backend/apps/knowledge/views.py#L752) | `UploadCallbackView` |
| I2 | `safe_delay(index_document_task, …)` | [views.py:926](backend/apps/knowledge/views.py#L926) | `TextUploadView` |
| I3 | `safe_delay(index_document_task, …)` | [views.py:1354](backend/apps/knowledge/views.py#L1354) | `FileUploadView` |
| I4 | `safe_delay(index_document_task, …)` | [views.py:1568](backend/apps/knowledge/views.py#L1568) | `BatchFileUploadView`（每文件一次） |
| I5 | `safe_delay(index_document_task, …)` | [chunked_views.py:342](backend/apps/knowledge/chunked_views.py#L342) | `ChunkMergeView` |
| I6 | `safe_delay(index_document_task, …)` | [crawler.py:491](backend/utils/crawler.py#L491) | 官网爬虫 |
| I7 | `index_document_task.run(None, doc.id)` | [scripts/index_all_docs.py:105](backend/scripts/index_all_docs.py#L105) | **绕过 Celery 直接同步索引全部 `status='uploaded'` 文档** |
| I8 | `index_document_task.run(None, doc.id)` | [scripts/index_quick.py:25](backend/scripts/index_quick.py#L25) | 同上（前 3 篇） |
| I9 | `index_document_task.run(None, doc.id)` | [seed_test_docs.py](backend/scripts/seed_test_docs.py) | 种子脚本 `--index` |

> **I7 特别提示**：`scripts/index_all_docs.py` 会对**所有** `status='uploaded'` 的文档批量同步索引——在 Phase 1 引入隔离区后，这个脚本会把隔离区里的文档一并索引。它必须在 Phase 1 一并改造（或加入 `--dry-run` 保护）。

### 8.5 孤儿索引任务（死代码，含真实路径穿越）

[config/celery.py:67-175](backend/config/celery.py#L67) 存在**第二份、独立的**索引实现：

```python
@app.task(bind=True, max_retries=3, ...)
def index_document(self, doc_id: int):        # 注意：不是 index_document_task
    ...
    doc.status = 'processing'                  # L108 ← 不在 STATUS_CHOICES 中
    tmp_path = f'/tmp/campus_index_{doc_id}_{doc.file_name}'   # L112 ← 路径穿越
    ...
    doc.status = 'indexed'                     # L131 / L154
    ...
    doc.status = 'error'                       # L163 ← 不在 STATUS_CHOICES 中
```

**事实**：
- 该任务**未出现在 `beat_schedule`**（[celery.py:30-53](backend/config/celery.py#L30)）；
- **全仓库无任何调用方**（grep `index_document\b` 仅命中其自身定义与 [views.py:745](backend/apps/knowledge/views.py#L745) 的一行注释 `# index_document.delay(doc_id=new_doc.id)`）；
- 但它确实被注册到 Celery app（`@app.task`），因此**可通过 broker 按名 `campus_knowledge.index_document` 投递执行**（`CELERY_ACCEPT_CONTENT=['json']`，无任务白名单/路由限制）。

**风险**：该任务把 `doc.file_name`（**客户端完全可控**，来自 `uploaded_file.name`，见 [views.py:1309](backend/apps/knowledge/views.py#L1309)）直接拼进 `/tmp/campus_index_{doc_id}_{file_name}`。若 `file_name = '../../../../etc/cron.d/x'`，则 `open(tmp_path,'wb')` 会在 worker 权限下**任意路径写文件**。同时它写 `status='processing'/'error'` 两个非法枚举值，会污染状态机；且它**不写 DocChunk**，只写 Milvus，会使引用溯源失败。

> 当前不可达（无调用方），故定级 HIGH 而非 CRITICAL；但属于必须清理的攻击面。

---

## 9. RAG 与聊天引用审计

### 9.1 进入向量库的条件

**唯一条件：`index_document_task` 被成功执行。**没有审核条件、没有来源校验（爬虫内容与用户上传一视同仁）。

### 9.2 检索时的过滤条件

统一入口 [utils/rag.py:587](backend/utils/rag.py#L587)：

```python
def authorized_knowledge_docs(user_context=None):
    qs = KnowledgeDoc.objects.all()
    if role == 'super_admin':
        return qs                              # ← 超管不过滤 visibility
    qs = qs.filter(visibility='public')        # ← 非超管仅公开
    school_level = Q(college_id__isnull=True, department_id__isnull=True)
    # admin/college_admin/dept_admin/user → school_level | 本院/本部门 | 本人上传
```

所有检索分支统一叠加 `status='indexed'`：

| 检索分支 | 位置 | 过滤条件 |
|---|---|---|
| 关键词检索（MySQL） | [rag.py:465](backend/utils/rag.py#L465) | `authorized_knowledge_docs(...).filter(kw_query, status='indexed')` |
| SQL 硬过滤 | [rag.py:635](backend/utils/rag.py#L635) | `authorized_knowledge_docs(...).filter(status='indexed')` |
| Top-K 权限过滤 | [rag.py:676](backend/utils/rag.py#L676) | `.filter(id__in=doc_ids, status='indexed')` |
| 元信息补全 | [rag.py:698](backend/utils/rag.py#L698) | `KnowledgeDoc.objects.filter(id__in=doc_ids, status='indexed')` |
| 图片检索 | [rag.py:860](backend/utils/rag.py#L860) | `file_type__in=image_types, status='indexed'`（**注意 [rag.py:827](backend/utils/rag.py#L827) 注释声明此处「不按 status='indexed' 过滤」——代码与注释不一致，以代码为准：L860 有该过滤，需一并复核）** |
| 引用构造 | [sse.py:232](backend/utils/sse.py#L232) | `authorized_knowledge_docs(context).filter(id__in=doc_ids, status='indexed')` |

### 9.3 结论：`status='indexed'` 是唯一条件

> **是。`status == 'indexed'`（叠加 visibility/college/department 权限过滤）是文档与切片进入 RAG 与聊天引用的唯一状态条件。**
>
> **不存在任何 `is_sensitive` / `sensitive_checked` / moderation 条件。** 因此：
> - 任何成功调用 `index_document_task` 的文档即进入 RAG；
> - 由于 `index_document_task` 无审核闸门，**不存在“审核中/被拒绝”的文档无法被检索的保证**；
> - 结合 §5.4，文档所有者可用 `PUT /api/knowledge/docs/{id}/ {"status":"indexed"}` 直接推进状态门。

### 9.4 聊天引用的暴露面

[utils/sse.py:249-285](backend/utils/sse.py#L249) `build_references_events`：

| 角色 | 返回内容 |
|---|---|
| `user`（普通用户） | 仅 `{"count": N, "summary": "参考了N份文档"}` |
| `admin` / `college_admin` / `dept_admin` / `super_admin` | `doc_id` / `doc_title` / `source_type` / `file_name` / `page` / `chunk_index` / `score` / **`content_snippet`（切片正文前 200 字）** / `source_url` / **`download_url`（签名 300s）** |

- 授权校验**在生成签名 URL 之前**执行（sse.py:227-232），这点实现正确；
- 但校验条件同样只有 `status='indexed'`，**无审核条件**；
- 检索到的**切片正文会被注入 LLM prompt**（`_enrich_with_doc_meta` + prompt 构造），**文档正文未经任何净化即进入模型上下文 → 存储型提示词注入（prompt injection）风险**。

### 9.5 无审核条件的 RAG 后果

被成功索引的任意文档，其切片正文会：
1. 被 `llm_client.embed` 向量化并存入 Milvus；
2. 被 RAG 召回后**注入 LLM 上下文**参与回答生成；
3. 以 `content_snippet` 形式**回传给管理员角色**，并附 300 秒签名下载链接。

**在当前实现下，一个包含涉密内容的上传文件，从上传成功到被全体授权用户问答引用，全程无任何拦截点。**

### 9.6 第二处引用读取路径（易漏审）

除 SSE 流式引用外，还存在**独立的历史消息引用接口**：

| 项 | 内容 |
|---|---|
| 入口 | `MessageViewSet.references` — `GET /api/chat/messages/{id}/references/` |
| 代码位置 | [apps/chat/views.py:178-237](backend/apps/chat/views.py#L178) |
| 权限 | `CanViewReferences`（仅 `admin` / `super_admin`，[chat/views.py:190](backend/apps/chat/views.py#L190)） |
| 过滤条件 | `status='indexed'` + 管理范围（[chat/views.py:216-219](backend/apps/chat/views.py#L216)），**无审核条件** |
| 返回 | 用 `KnowledgeDocListSerializer`（不含 `oss_key`/`download_url`）+ `content_snippet` + `freshness_date` |
| 结论 | 与 SSE 路径同源缺陷：**只按 `status='indexed'` 放行**，不检查 `is_sensitive` / 审核状态 |

**引用内容被持久化**：`ChatMessage.references`（JSONField，[apps/chat/models.py:44](backend/apps/chat/models.py#L44)）保存完整的 `references_detail`（[chat/views.py:520-523](backend/apps/chat/views.py#L520)），**其中包含 `content_snippet`（切片正文）与 `download_url`（签名 URL）**。即违规内容的正文片段与一个签名链接会被长期写入 MySQL，且随聊天历史接口反复返回。签名 URL 本身 300 秒后失效，但**正文片段永久留存**——Phase 1 若实施"事后下架"，必须同时考虑清理历史 `references`。

### 9.7 关于 Milvus 孤儿向量的核查结论（澄清，非风险）

`KnowledgeDocDeleteView` 的文档注释明确承认「**Milvus 向量暂不同步删除（TODO）**」（[views.py:288](backend/apps/knowledge/views.py#L288)），且 `perform_destroy` 确实只删 OSS + MySQL。经核查**该残留不会经 RAG 泄露**：

- `retrieve()` 在调用 Milvus 前先取 MySQL 候选 ID：`candidate_ids = _apply_hard_filters(...)`（[rag.py:946](backend/utils/rag.py#L946)），而 `_apply_hard_filters` 内为 `authorized_knowledge_docs(user_context).filter(status='indexed')`（[rag.py:635](backend/utils/rag.py#L635)）；
- **若 `candidate_ids` 为空，`retrieve()` 直接走标题降级搜索并返回，不会调用 Milvus**（[rag.py:948-952](backend/utils/rag.py#L948)）；
- Milvus 检索唯一的调用点始终传入非空 `filter_ids=candidate_ids`（[rag.py:961](backend/utils/rag.py#L961)），Milvus 层的 `expr = f'doc_id in {valid_ids}'` 生效（[milvus.py:212-217](backend/utils/milvus.py#L212)）；且 Milvus 层**不**做任何状态/可见性判断，完全依赖 SQL 侧候选集（[milvus.py:201-239](backend/utils/milvus.py#L201)）。

**结论**：已删除文档的残留向量不可被召回。**但这构成一个隐性耦合**：审核安全**完全依赖 SQL 侧候选集这一处**。Phase 1 若在 SQL 侧叠加审核条件（推荐做法），Milvus 残留同样被自动挡住；但若有任何新代码直接调用 `milvus_client.search` 而不传 `filter_ids`，则**所有历史残留向量（含已删除、已下架、未审核内容）会立即暴露**。建议 Phase 1 在 `milvus_client.search` 内对空 `filter_ids` 直接返回空结果，把这一约束固化到基础设施层。

---

## 10. STS 直传审计

### 10.1 STS 签发（`UploadSTSView` → `OSSClient.get_sts_credentials`）

[views.py:613-648](backend/apps/knowledge/views.py#L613) → [oss.py:127-178](backend/utils/oss.py#L127)

| 必答问题 | 结论 | 证据 |
|---|---|---|
| **STS 是否允许客户端指定 object key？** | **否（当前）** | key 由服务端 `_generate_oss_key` 生成（[oss.py:140](backend/utils/oss.py#L140)），客户端只拿到 `oss_key` + `upload_url`，无法用签名 URL 改写 key |
| **STS 是否限制 prefix？** | **未生效（当前无真实 STS）** | 见下 |
| **STS 是否可能写正式 `knowledge/` 前缀？** | **是，且只能写正式前缀** | `_generate_oss_key` 固定返回 `knowledge/{scope}/{owner_id}/{YYYY-MM}/{uuid}.{ext}`（[oss.py:122](backend/utils/oss.py#L122)）；**无隔离前缀可用** |
| **STS policy 是否由服务端生成？** | **否 —— STS 根本未实现** | 见下 |
| **callback 是否验证真实对象？** | **否** | 见 §10.3 |

### 10.2 ★ 关键发现：STS 是空壳实现，实际交付的是“预签名 PUT URL”

[oss.py:152-178](backend/utils/oss.py#L152) 全文：

```python
# 生产环境：调用 STS AssumeRole 获取临时凭证
# NOTE: 需要安装 alibabacloud_sts20150401，STS Policy 限定上传路径前缀
try:
    # from alibabacloud_sts20150401.client import Client as StsClient
    # ... 整段 STS AssumeRole 代码全部被注释 ...
    # policy = {
    #     "Statement": [{"Effect": "Allow", "Action": ["oss:PutObject"],
    #         "Resource": [f"acs:oss:*:*:{self.bucket_name}/knowledge/{college_id}/*"]}],
    #     "Version": "1"}
    upload_url = self.bucket.sign_url('PUT', oss_key, 300)     # ← 实际执行的分支
    return {
        'oss_key': oss_key,
        'upload_url': upload_url,
        'expiration': int(time.time()) + 300,
        'sts_credentials': None,  # 部署时替换为真实 STS 凭证
        'dev_mode': False,
    }
```

**事实**：
1. **生产分支与开发分支行为完全相同**——都返回 `sign_url('PUT', oss_key, 300)`，都没有真实 STS 凭证（`sts_credentials: None`）；
2. `if not self.access_key_id` 判断（[oss.py:142](backend/utils/oss.py#L142)）只是把 `dev_mode` 标为 `True/False`，**返回结构一致**；
3. `alibabacloud_sts20150401` 未在 [requirements.txt](backend/requirements.txt) 中（经确认）；
4. 因此**当前不存在 STS policy，也不存在按 prefix 的权限收窄**。

**当前实际的安全边界（由预签名 PUT URL 的性质决定）**：

| 维度 | 现状 | 评价 |
|---|---|---|
| 客户端能否改 key | 否（签名绑定了 key） | ✅ 当前安全 |
| URL 有效期 | 300 秒 | ✅ |
| **是否限制上传大小** | **否**（`sign_url` 未设置 `Content-Length` 条件） | ❌ 可上传任意大小 |
| **是否限制 Content-Type** | **否**（未设置 `Content-Type` 条件） | ❌ 客户端可自选 MIME，绕开 `guess_content_type` 的防混淆意图，可上传 `text/html` 触发存储型 XSS |
| **是否一次性** | **否**（300 秒内可无限次复用、可多次覆盖同一 key） | ⚠️ |
| **是否绑定发起用户** | **否**（URL 可转发给任意第三方，第三方在 300 秒内可写入该 key） | ⚠️ |
| **是否校验 MD5** | **否**（服务端只在 callback 里**记录**客户端自报的 md5，不做内容校验） | ❌ |

### 10.3 ★ callback 不验证真实对象（CRITICAL）

[views.py:680-757](backend/apps/knowledge/views.py#L680) + `_validate_oss_key` [views.py:767-775](backend/apps/knowledge/views.py#L767)：

```python
@staticmethod
def _validate_oss_key(oss_key: str) -> None:
    """校验 oss_key 格式合法性，防止路径穿越攻击"""
    if '..' in oss_key:      raise ValidationError(...)
    if oss_key.startswith('/'): raise ValidationError(...)
    if not oss_key.startswith('knowledge/'): raise ValidationError(...)
```

**逐条回答需求中列出的 callback 绕过检查项：**

| 检查项 | 现状 | 后果 |
|---|---|---|
| **伪造 `oss_key`** | ❌ **未防护** | 服务端**不校验对象是否存在**（无 `head_object`）、**不校验对象归属**（不比对 `knowledge/{scope}/{owner_id}` 与请求者角色/学院）。任意 `knowledge/` 下的 key 均可被采纳 |
| **伪造 `file_size`** | ❌ **未防护** | 直接写入 `KnowledgeDoc.file_size`，不比对 OSS 实际大小 |
| **伪造 `content-type` / `file_type`** | ❌ **未防护** | 直接 `normalize_file_type(data['file_type'])` 落库，不比对 OSS 对象的实际 `Content-Type` |
| **伪造 `md5`** | ❌ **未防护** | 直接落库为 `md5_hash`，不重算对象 MD5。可用于**污染秒传去重索引**（见 R9） |
| **重复 callback** | ⚠️ **未防护** | 无幂等键；同一 `oss_key` 可反复提交生成多条 `KnowledgeDoc`（受 MD5 去重部分缓解，但 md5 可伪造，故无实质防护） |
| **越权 callback** | ❌ **未防护** | 见下 |
| **对象是否存在** | ❌ **未校验** | 可创建指向不存在对象的“幽灵文档” |

**验证依据**：`KnowledgeDocDeleteView.perform_destroy` [views.py:296-302](backend/apps/knowledge/views.py#L296) 无归属校验，直接 `oss_client.delete(instance.oss_key)`；而 `get_queryset` 用 `_filter_deletable` 判定，攻击者因 `uploader_id==user.id` 而命中放行分支（[views.py:315-329](backend/apps/knowledge/views.py#L315)）。

---

#### 攻击路径 A：MD5 秒传索引投毒（**无需任何机密信息**，已完整验证）

这是本次审计中最容易复现的一条路径，不依赖获取他人的 `oss_key`：

```
1. 攻击者（任一 admin/college_admin/dept_admin）正常上传一份自己的文件 F1，
   在 POST /api/knowledge/upload/callback/ 中把 md5 填为**目标受害者文件的真实 MD5** M_v
   （M_v 可从受害者上传后暴露的文档记录中读取：GET /docs/{id}/ 返回 md5_hash 字段）

2. 服务端 OSSClient.check_duplicate(M_v)（oss.py:281）命中 → 返回 duplicate=True
   攻击者由此确认 M_v 已被占用，并得知其 doc_id

3. 此后受害者再次上传**内容完全相同**的文件：
   ├─ 走 File/Batch 路径：check_duplicate 命中 → 视图跳过创建，受害者上传"成功"但
   │  实际未被处理（[views.py:1246](backend/apps/knowledge/views.py#L1246) 附近分支）
   └─ 走**分片 merge 路径**：更严重 —— check_duplicate 命中后
      `oss_client.delete(session.oss_key)` **删除刚刚上传的对象**
      （[chunked_views.py:292-299](backend/apps/knowledge/chunked_views.py#L292)）

4. 结果：受害者上传的合法文件被静默丢弃（分片路径下对象被物理删除），
   而 `md5_hash` 索引与真实内容不再对应
```

根因：`md5` / `file_size` / `file_type` / `oss_key` 全部由客户端提交，服务端**从不重算 MD5、不 `head_object` 核对大小与 Content-Type**。`md5_hash` 字段因此整体不可信，而它却是秒传去重与"上传成功"判定的唯一依据。

---

#### 攻击路径 B：基于 `oss_key` 认领的接管（**需先获得他人 oss_key**）

```
1. 攻击者（admin/college_admin/dept_admin）从 GET /api/knowledge/docs/{id}/
   取得某文档的 oss_key。
   ★ 注意：KnowledgeDocSerializer.fields 含 'oss_key'、'oss_url'（serializers.py:27），
     且该视图仅要求 IsAuthenticated（views.py:248），
     且 _admin_scope_filter **不过滤 visibility**（见 R26）——
     因此校级(scope=school)的 private 文档，其 oss_key 对**所有**管理员可见。

2. POST /api/knowledge/upload/callback/ 提交该 oss_key + 任意伪造的 file_size/file_type/md5。

3. _validate_oss_key 通过（前缀合法、无 ".."）→ md5 去重未命中（md5 是伪造的）
   → KnowledgeDoc.objects.create(oss_key=他人的 key, uploader_id=攻击者自身)
   ★ 攻击者由此成为该对象的"上传者"

4. 攻击者获得：
   ├─ GET  /docs/{new_id}/download/  → 该对象的签名下载 URL（7 天）
   ├─ GET  /docs/{new_id}/preview/   → 文本类直接回传对象正文
   ├─ DELETE /docs/{new_id}/         → oss_client.delete(该 oss_key)  ★物理删除他人对象
   └─ 索引任务被触发 → 该对象内容进入攻击者的可检索范围
```

**关于影响范围的准确界定**：`_filter_deletable`（[views.py:315-329](backend/apps/knowledge/views.py#L315)）对 `admin` 放行「校级 + 本院 + 本人上传」，因此攻击者**本就能删除**其管理范围内的对象——路径 B 在这些情形下是「绕过保管链并制造不可追溯的删除」，而非权限提升。真正的**跨学院**提升需要攻击者先获得范围外的 `oss_key`，而 `oss_key` 的暴露本身受范围约束（已核对：列表序列化器不含 `oss_key`；下载/预览视图均经 `_admin_scope_filter`），因此**跨学院路径未能以单接口闭环证明**。但以下暴露渠道使其变为现实威胁：日志（`logger.info(f'OSS 上传成功 oss_key={oss_key}')` [views.py:1206](backend/apps/knowledge/views.py#L1206)）、签名 URL 的 path 段（浏览器历史/Referer/反向代理访问日志）、`oss_url` 字段、以及离职/转岗人员的既有可见性。

> **定级仍为 CRITICAL**，但理由需要准确陈述：不是"任意 OSS 对象一行接管"，而是**服务端对 `oss_key` 完全不做存在性/归属/大小/类型/哈希校验**。这同时意味着 —— **Phase 1 若希望在"上传来源合法性"上建立任何门控（例如"只有服务端签发的、绑定到本次会话的 oss_key 才允许建库"），当前 callback 的输入面使该门控无法建立**。这也是 Phase 1 必须优先封堵此接口的原因：它是审核链路的上游入口，不封堵则下游审核可被绕过。

### 10.4 STS 相关问题小结

| 序号 | 问题 | 严重度 |
|:-:|---|:-:|
| S1 | STS 未实现，注释代码冒充实现；`sts_credentials` 恒为 `None`，接口文档（views.py:609）却宣称返回 STS 凭证 | HIGH |
| S2 | 预签名 PUT URL 无大小、无 Content-Type、无一次性、无用户绑定约束 | HIGH |
| S3 | callback 不验证对象存在/归属/大小/类型/MD5 | **CRITICAL** |
| S4 | 客户端直写正式 `knowledge/` 前缀，无隔离区 | **CRITICAL** |

---

## 11. 分片上传审计

### 11.1 链路

```
POST /upload/chunk/init/    ChunkUploadInitView
  ├─ 校验 file_md5 格式（32位hex）                          [chunked_views.py:114]
  ├─ 校验 filename 非空                                     [L116]
  ├─ 校验扩展名 ∈ ALLOWED_FILE_TYPES                        [L119-123]
  ├─ ❌ 未校验 file_size（FILE_SIZE_LIMITS 未应用）          ★见 R6
  ├─ 秒传：check_duplicate(file_md5)                         [L132]
  ├─ 断点续传：查 ChunkedUploadSession(user, md5, uploading) [L142]
  └─ bucket.init_multipart_upload(oss_key, Content-Type)     [L162]
       oss_key = knowledge/{scope}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}   [L53]
  ▼
POST /upload/chunk/         ChunkUploadView
  ├─ session = ChunkedUploadSession.filter(upload_id, user, status='uploading')  [L209] ✅ 归属校验
  ├─ chunk_index ∈ [0, 10000)                                [L206]
  └─ bucket.upload_part(session.oss_key, upload_id, chunk_index+1, chunk_file)   [L219]
       ❌ 无单分片大小上限
  ▼
POST /upload/chunk/merge/   ChunkMergeView
  ├─ session 校验（同 user）                                  [L249] ✅
  ├─ file_md5 与 init 时一致                                  [L257]
  ├─ parts = _list_uploaded_parts()（OSS 实际分片）            [L261]
  ├─ 非空校验                                                 [L267]
  ├─ total_size == session.file_size ?                        [L270-274]
  │    ❌ session.file_size 来自客户端；若为 0 则整个检查被跳过
  ├─ bucket.complete_multipart_upload(...)                    [L278]
  ├─ session.status='completed'                               [L288]
  ├─ 二次 MD5 去重 → 命中则 oss_client.delete(session.oss_key) [L292-299]
  └─ KnowledgeDoc.objects.create(...) → safe_delay(index)     [L318, L342]
```

### 11.2 逐项回答

| 检查项 | 结论 |
|---|---|
| **只审核分片？** | ❌ 分片、合并、最终对象**均无审核** |
| **merge 后不审核？** | ❌ **确认：merge 后无审核**，直接建 `KnowledgeDoc` 并触发索引 |
| **缺失分片？** | ⚠️ **部分防护**。仅以「分片总大小 == 客户端自报 file_size」判定（[L270](backend/apps/knowledge/chunked_views.py#L270)）。客户端在 init 时报小、merge 时传 0 即完全跳过；且**不校验分片号连续性**、**不校验 per-part ETag/大小**、**不重算最终对象 MD5** |
| **重复 merge？** | ⚠️ `status='uploading'` 过滤提供了弱幂等；并发双 merge 下第二个 `complete_multipart_upload` 会抛 `OssError` 返回 500（[L281-286]），但两请求均已通过 `status` 检查，存在竞态窗口 |
| **并发 merge？** | ⚠️ 同上，无数据库锁/乐观锁（`select_for_update` 未使用） |
| **审核与索引顺序？** | ❌ merge 函数体内 `create` 与 `safe_delay(index)` 相邻执行（[L318](backend/apps/knowledge/chunked_views.py#L318) → [L342](backend/apps/knowledge/chunked_views.py#L342)），中间无任何审核步骤 |

### 11.3 分片上传特有问题

| 序号 | 问题 | 严重度 |
|:-:|---|:-:|
| C1 | **无单文件大小上限**：`FILE_SIZE_LIMITS` 未在 `ChunkUploadInitView` 中应用，可在 OSS 写入任意大小对象 | HIGH |
| C2 | **无单分片大小上限**：`ChunkUploadView` 对 `chunk_file` 无大小检查 | HIGH |
| C3 | merge 后无审核，直接建文档 + 索引 | **CRITICAL** |
| C4 | `file_size` 完整信任客户端，`file_size=0` 时完全跳过大小的完整性检查 | MEDIUM |
| C5 | 最终对象 MD5 从未在服务端重算，`md5_hash` 完全不可信 | MEDIUM |
| C6 | 超时会话清理任务（[tasks.py:269](backend/apps/knowledge/tasks.py#L269)，每天 03:30）会 `abort_multipart_upload`，行为正确 ✅ | — |

---

## 12. 安全绕过矩阵

> **填表原则**：以代码证据为准；无证据写「未确认」。所有“经过敏感检测”列均为「否」，因为 `exp 1-11` 的全部入口**均未调用** `utils/sensitive_filter.check_sensitive`（该函数仅有 2 个调用点，都在 chat 提问侧）。

| 路径 | 是否经过敏感检测 | 是否病毒扫描 | 是否 LLM/视觉审核 | 是否隔离 | 是否可能直接索引 | 风险 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| 普通上传 `upload/file/` | ❌ 否 | ❌ 否 | ⚠️ 仅生成元数据，非审核 | ❌ 否 | ✅ **是**（L1354 直接触发） | **CRITICAL** |
| 批量上传 `upload/batch/` | ❌ 否 | ❌ 否 | ⚠️ 同上 | ❌ 否 | ✅ **是**（L1568 每文件触发） | **CRITICAL** |
| 文本上传 `upload/text/` | ❌ 否 | N/A | ⚠️ 同上 | ❌ 否 | ✅ **是**（L926 直接触发） | **CRITICAL** |
| STS `upload/sts/` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ⚠️ 否（自身不建文档）<br>**但客户端可直写正式前缀** | HIGH |
| callback `upload/callback/` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ✅ **是**（L752 直接触发）<br>**且可伪造任意 oss_key** | **CRITICAL** |
| 分片 init `upload/chunk/init/` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | HIGH（无大小上限） |
| 分片接收 `upload/chunk/` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | HIGH（无单分片上限） |
| 分片 merge `upload/chunk/merge/` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ✅ **是**（L342 直接触发） | **CRITICAL** |
| AI 分类 `upload/ai-classify/`（别名 `upload/classify/`） | ❌ 否 | N/A | ⚠️ 仅生成元数据 | N/A | ❌ 否（不落库不落 OSS） | LOW（可作未授权 LLM/视觉算力消耗入口） |
| 语音问答 `chat/voice-ask/` | ✅ **是**（转写文本过 `check_sensitive`，[chat/views.py:991](backend/apps/chat/views.py#L991)） | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否（不落库） | LOW |
| 历史消息引用 `chat/messages/{id}/references/` | ❌ 否 | N/A | ❌ 否 | ❌ 否 | ⚠️ 仅返回已索引文档的引用（`status='indexed'`，[chat/views.py:216](backend/apps/chat/views.py#L216)），**无审核条件** | MEDIUM |
| 官网爬虫（beat / 管理命令 / FAQ 任务） | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ✅ **是**（[crawler.py:491](backend/utils/crawler.py#L491)） | HIGH（外部内容无审核入库，且每日自动执行） |
| Django Admin `/admin/` | N/A | N/A | N/A | N/A | ❌ 否（KnowledgeDoc 未注册） | — |
| 内部 API `/api/admin/**` `/api/resources/**` | N/A | N/A | N/A | N/A | ❌ 否（无上传端点，仅日志读写） | — |
| 运维脚本 `scripts/index_all_docs.py` | ❌ 否 | N/A | ❌ 否 | ❌ 否 | ✅ **是**（批量索引全部 `status='uploaded'`） | HIGH（Phase 1 必须同步改造） |
| 孤儿任务 `campus_knowledge.index_document` | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ⚠️ **可通过 broker 按名投递**（无调用方） | HIGH |
| 图片（所有入口） | ❌ 否 | ❌ 否 | ⚠️ 仅生成元数据 | ❌ 否 | ⚠️ 图片不入 Milvus 正文，但**元数据+文件可读** | HIGH |
| PDF/Office（所有入口） | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ✅ **是**（正文切片入 Milvus） | **CRITICAL** |
| 压缩包（所有入口） | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否 | ❌ 否（无提取器，正文不入 Milvus）<br>**但文件本身未审即可下载** | MEDIUM |

---

## 13. 风险清单

### CRITICAL

---

**R1：callback 接口可用任意 `oss_key` 接管/删除他人物件（对象引用伪造）**

| 项 | 内容 |
|---|---|
| 风险 | `UploadCallbackView` 把客户端提交的 `oss_key` 当作可信输入，不校验对象存在性、归属、大小、类型与哈希，导致元数据可伪造、`md5_hash` 去重索引可投毒、他人对象可被“认领”为本人文档 |
| 文件 | `apps/knowledge/views.py` |
| 函数/类 | `UploadCallbackView.post` / `UploadCallbackView._validate_oss_key` |
| 代码位置 | [views.py:687](backend/apps/knowledge/views.py#L687)（调用校验）、[views.py:767-775](backend/apps/knowledge/views.py#L767)（校验实现）、[views.py:715-730](backend/apps/knowledge/views.py#L715)（创建） |
| 触发条件 | 已登录且具备 `IsAdminOrSuperAdmin`（[views.py:678](backend/apps/knowledge/views.py#L678)）；路径 A **无需任何机密信息**，路径 B 需持有一个合法 `oss_key` |
| 实际影响 | ① **路径 A**：伪造 `md5` 投毒秒传去重索引 → 受害者合法上传被静默丢弃；分片 merge 路径下更会导致 `oss_client.delete()` **物理删除受害者刚上传的对象**（[chunked_views.py:294](backend/apps/knowledge/chunked_views.py#L294)）；② **路径 B**：认领可见范围内的对象，取得 7 天下载 URL、文本正文、并物理删除该对象，且删除行为挂在自己名下、原上传者的保管链被切断；③ 伪造 `file_size`/`file_type`/`title` 污染知识库元数据；④ 可为**不存在的**对象建立文档记录并触发索引 |
| 攻击路径 | 见 §10.3 路径 A（完整可复现）与路径 B |
| 建议修复方向 | callback 必须基于**服务端上传会话表**（新增 `PendingUpload(oss_key, user, expected_size, expected_md5, expires_at, consumed)`）校验：oss_key 必须由服务端签发、`user` 匹配、未消费；并调用 `head_object` 校验对象真实存在、大小与 Content-Type 与申报一致；校验 `oss_key` 的 `{scope}/{owner_id}` 与请求者角色归属一致；禁止客户端提交 `md5` 作为去重依据（服务端重算或使用 OSS 侧校验值） |

---

**R2：系统中不存在任何“内容安全审核”环节（含全部 10 个入口）**

| 项 | 内容 |
|---|---|
| 风险 | 所有上传路径均无内容审核：无敏感检测、无病毒扫描、无图片审核、无 PDF/Office 文本审核；上传成功即写正式存储、即建文档、即触发索引 |
| 文件 | `apps/knowledge/views.py`、`apps/knowledge/chunked_views.py`、`utils/crawler.py` |
| 函数/类 | `FileUploadView.post` / `BatchFileUploadView.post` / `TextUploadView.post` / `UploadCallbackView.post` / `ChunkMergeView.post` / `crawl_and_index` |
| 代码位置 | [views.py:1354](backend/apps/knowledge/views.py#L1354)、[views.py:1568](backend/apps/knowledge/views.py#L1568)、[views.py:926](backend/apps/knowledge/views.py#L926)、[views.py:752](backend/apps/knowledge/views.py#L752)、[chunked_views.py:342](backend/apps/knowledge/chunked_views.py#L342)、[crawler.py:491](backend/utils/crawler.py#L491) |
| 触发条件 | 无需特殊条件，正常上传即可 |
| 实际影响 | 违规、涉密、恶意文件在上传后 **1 个异步任务周期内**完成分块、向量化并进入 RAG，可被全体授权用户问答引用与下载 |
| 攻击路径 | 上传含涉密内容的 PDF → 等待索引 → 任何被授权的用户提问相关内容，即由 LLM 引用该文档回答 |
| 建议修复方向 | 引入统一 `upload_pipeline`：`OSS 隔离区落盘 → 内容提取 → 多路审核（规则/LLM/视觉/AV）→ 审核通过才 OSS 转正 + 建 KnowledgeDoc + 触发索引`；审核闸门必须落在 `index_document_task` 任务体内 |

---

**R3：审核状态字段（`is_sensitive` / `sensitive_checked`）完全未被使用，且可由客户端写入**

| 项 | 内容 |
|---|---|
| 风险 | 两个字段既无写入方也无读取方（死字段），却可通过 `PUT /api/knowledge/docs/{id}/` 由文档所有者任意赋值 |
| 文件 | `apps/knowledge/models.py`、`apps/knowledge/serializers.py`、`apps/knowledge/views.py` |
| 函数/类 | `KnowledgeDoc`、`KnowledgeDocSerializer.Meta`、`KnowledgeDocDetailView.perform_update` |
| 代码位置 | [models.py:127-128](backend/apps/knowledge/models.py#L127)、[serializers.py:23-43](backend/apps/knowledge/serializers.py#L23)、[views.py:257-262](backend/apps/knowledge/views.py#L257) |
| 触发条件 | 文档所有者（或超管）对自有文档发 PUT |
| 实际影响 | ① 当前：`status` 可被直接置为 `indexed`，绕过 RAG 状态门；`visibility` 可被改为 `public`，使私密文档进入检索范围；② **对未来：若 Phase 1 把审核结论存在这两个字段上，该审核可被一行 PUT 绕过** |
| 攻击路径 | `PUT /api/knowledge/docs/{id}/ {"status":"indexed","is_sensitive":false,"sensitive_checked":true,"visibility":"public"}` |
| 建议修复方向 | Phase 1 将 `status` / `is_sensitive` / `sensitive_checked`（及新增审核字段）全部加入 `read_only_fields`；审核字段仅允许 moderation 服务写入；`status` 的推进只允许由 `index_document_task` 内部完成 |

---

**R4：不存在隔离存储，所有写入直落正式前缀；OSS 封装不支持 copy/move**

| 项 | 内容 |
|---|---|
| 风险 | 无 `quarantine/` 隔离区；`knowledge/` 前缀即正式区；客户端（STS）可直写该前缀；`OSSClient` 无 copy/move，转正机制无基础设施 |
| 文件 | `utils/oss.py`、`apps/knowledge/views.py`、`apps/knowledge/chunked_views.py` |
| 函数/类 | `OSSClient._generate_oss_key` / `get_sts_credentials`，`_build_oss_key` |
| 代码位置 | [oss.py:108-122](backend/utils/oss.py#L108)（key 生成）、[oss.py:140-175](backend/utils/oss.py#L140)（STS）、[chunked_views.py:42-53](backend/apps/knowledge/chunked_views.py#L42) |
| 触发条件 | 任意上传 |
| 实际影响 | 未经审核的文件与已审核文件混在同一前缀；一旦 OSS 桶存在匿名/宽松读策略或 URL 泄露，未审文件可直接被读取；Phase 1 无法实施“先隔离后转正” |
| 攻击路径 | STS 直传 → 客户端直接写入 `knowledge/school/0/…` |
| 建议修复方向 | 新增 `quarantine/{user_id}/{uuid}` 前缀与写入点；`utils/oss.py` 增加 `copy_object` / `promote_object` 封装；`KnowledgeDoc.oss_key` 在审核通过时由 quarantine key 改为正式 key（需评估该字段当前为 read-only 的影响） |

---

**R5：索引任务入口无任何审核闸门 + 同步降级使绕过成为必然**

| 项 | 内容 |
|---|---|
| 风险 | `index_document_task` 仅校验文档存在即开始解析→分块→向量化→入库；且 `safe_delay` 在 Celery 不可用时同步执行，`shared_task_compat` 在 celery 未安装时同样同步执行，二者共用同一任务体 |
| 文件 | `apps/knowledge/tasks.py`、`utils/celery_dispatcher.py`、`utils/shared_task_compat.py` |
| 函数/类 | `index_document_task`、`safe_delay`、`shared_task` |
| 代码位置 | [tasks.py:137-141](backend/apps/knowledge/tasks.py#L137)（唯一校验）、[celery_dispatcher.py:32-49](backend/utils/celery_dispatcher.py#L32)、[shared_task_compat.py:57-81](backend/utils/shared_task_compat.py#L57) |
| 触发条件 | Redis/Celery 不可用，或本地未安装 celery |
| 实际影响 | 审核尚未完成的文件被**立即、同步地**索引；用户收到 201 时文件已进入 Milvus |
| 攻击路径 | 制造 Redis 不可用（或利用部署缺 celery），随后正常上传 |
| 建议修复方向 | **审核校验必须做在 `index_document_task` 任务体内**（唯一对异步/同步两条路径都生效的插桩点），并显式返回“未通过审核，跳过索引”；同时移除或收敛 `ExtractFreshnessView` 的 `?async=false` 同步旁路对索引的影响 |

---

### HIGH

**R6：分片上传无大小限制**

- 文件：`apps/knowledge/chunked_views.py`
- 函数/类：`ChunkUploadInitView.post`、`ChunkUploadView.post`
- 代码位置：[chunked_views.py:106-180](backend/apps/knowledge/chunked_views.py#L106)（init 未调用 `_validate_file_size`）、[chunked_views.py:195-229](backend/apps/knowledge/chunked_views.py#L195)（分片无上限）
- 触发条件：走分片上传通道
- 实际影响：可向 OSS 写入任意大小的对象，造成存储成本失控与带宽滥用；配合 `file_size=0` 可完全跳过 merge 的大小一致性检查（[chunked_views.py:271](backend/apps/knowledge/chunked_views.py#L271)）
- 攻击路径：`chunk/init` 时 `file_size=0` → 循环 `chunk/` 上传海量分片 → `chunk/merge` 跳过校验
- 建议修复方向：init 时强制应用 `FILE_SIZE_LIMITS`（并拒绝 `file_size<=0`）；`ChunkUploadView` 增加单分片大小上限；`DATA_UPLOAD_MAX_MEMORY_SIZE` 之外单独配置分片阈值

**R7：签名 URL 有效期长达 7 天且不可撤销，削弱事后处置能力**

- 文件：`apps/knowledge/serializers.py`、`utils/oss.py`、`utils/crawler.py`（间接）
- 函数/类：`KnowledgeDocSerializer.get_download_url` / `get_oss_url`
- 代码位置：[serializers.py:45-57](backend/apps/knowledge/serializers.py#L45)（`expires=604800`）、[oss.py:201-247](backend/utils/oss.py#L201)、[data_query.py:257-265](backend/utils/data_query.py#L257)（`expires=3600`）、[sse.py:262-268](backend/utils/sse.py#L262)（`expires=300`）
- 触发条件：任意可读该文档的用户调用 `GET /api/knowledge/docs/{id}/`
- 实际影响：文档被隔离/删除/审核不通过后，此前签发的 URL 在最长 7 天内仍然有效；OSS 对象未真正删除前内容持续可访问。**审核系统的“下架”动作无法即时生效**
- 建议修复方向：统一收敛签名有效期（建议 ≤300s）；对已下架文档的 `oss_key` 执行物理删除或迁移到无签名的死区；设计“下架”动作时把“删除 OSS 对象”作为必要步骤

**R8：文档内容提取无超时/无资源限制（DOC 类型还会启动外部进程）**

- 文件：`apps/knowledge/extractor.py`
- 函数/类：`extract_text_from_oss`、`_parse_pdf`、`_parse_docx`、`_parse_pptx`、`_parse_xlsx`
- 代码位置：[extractor.py:49-73](backend/apps/knowledge/extractor.py#L49)（PDF 明确取消页数限制）、[extractor.py:177-221](backend/apps/knowledge/extractor.py#L177)（antiword / soffice subprocess）、[extractor.py:251-293](backend/apps/knowledge/extractor.py#L251)
- 触发条件：上传超大 PDF / 含巨量 sheet 的 XLSX / 恶意 DOC
- 实际影响：Celery worker 被长任务占满（`CELERY_TASK_TIME_LIMIT=300` 提供兜底，但仅对异步路径有效；同步降级路径无此保护）；antiword/LibreOffice 处理不可信输入且无沙箱
- 建议修复方向：PDF 页数/字符数上限；XLSX 行列上限；提取总耗时上限；对外部进程使用受限用户 + 只读挂载 + 网络禁用；统一提取入口并加固（当前 `FileUploadView` 另有一处内联提取，[views.py:1232-1253](backend/apps/knowledge/views.py#L1232)）

**R9：客户端自报的 md5 / size / type 被直接信任，秒传去重可被污染**

- 文件：`apps/knowledge/views.py`、`apps/knowledge/chunked_views.py`、`utils/oss.py`
- 函数/类：`UploadCallbackView.post`、`OSSClient.check_duplicate`
- 代码位置：[views.py:694-704](backend/apps/knowledge/views.py#L694)（回调去重）、[oss.py:281-293](backend/utils/oss.py#L281)、[chunked_views.py:292-299](backend/apps/knowledge/chunked_views.py#L292)（merge 去重）
- 触发条件：提交任意 `md5` 值
- 实际影响：① 可让上传的文件不被识别为重复；② 可让系统把刚上传的对象删除（`chunked_views.py:294`）并返回他人文档；③ `md5_hash` 字段整体失去可信度，无法用于内容一致性校验
- 建议修复方向：服务端对已落 OSS 的对象重算 MD5（或使用 OSS 侧 `Content-MD5` 校验），拒绝不一致；`md5_hash` 不作为唯一去重依据

**R10：STS 未实现（注释代码充当实现），接口契约与实际行为不符**

- 文件：`utils/oss.py`
- 函数/类：`OSSClient.get_sts_credentials`
- 代码位置：[oss.py:152-178](backend/utils/oss.py#L152)
- 触发条件：调用 `POST /api/knowledge/upload/sts/`
- 实际影响：接口文档（[views.py:609](backend/apps/knowledge/views.py#L609)）承诺返回 STS 临时凭证，实际 `sts_credentials` 恒为 `None`；前端若按契约分支处理会异常。更关键的是**不存在任何按 prefix 收窄权限的策略**，当前安全性完全依赖预签名 URL 的 key 绑定
- 建议修复方向：明确取舍——要么落地真实 STS（含 `Resource` 限定到用户专属隔离前缀），要么在文档与代码中一致地声明“使用预签名 PUT + 服务端绑定会话”，并补齐大小/类型/一次性约束

**R11：孤儿索引任务存在真实路径穿越，且写入非法状态值**

- 文件：`config/celery.py`
- 函数/类：`index_document`（区别于 `apps.knowledge.tasks.index_document_task`）
- 代码位置：[celery.py:112](backend/config/celery.py#L112)（`tmp_path = f'/tmp/campus_index_{doc_id}_{doc.file_name}'`）、[celery.py:108](backend/config/celery.py#L108)（`status='processing'`）、[celery.py:163](backend/config/celery.py#L163)（`status='error'`）
- 触发条件：向 broker 投递任务名 `campus_knowledge.index_document`（当前仓库无调用方，但任务已注册且无投递白名单）
- 实际影响：`doc.file_name` 客户端可控（[views.py:1309](backend/apps/knowledge/views.py#L1309)），构造 `../../` 可在 worker 权限下任意路径写文件；同时污染状态机（非法枚举值）、不写 `DocChunk` 导致引用溯源断裂
- 建议修复方向：删除该孤儿任务及其 `_parse_*` / `_chunk_text` 私有实现（与 `apps/knowledge/tasks.py` 完全重复）；若确有用途，改用 `tempfile.mkstemp` 并复用 `extractor.py`

**R12：图片与压缩包类文件完全无内容审核**

- 文件：`apps/knowledge/views.py`、`apps/knowledge/extractor.py`
- 函数/类：`_ai_classify`（视觉分支）、`_EXTRACTORS`
- 代码位置：[views.py:1704-1725](backend/apps/knowledge/views.py#L1704)（视觉提示词仅为标题/描述/关键词生成）、[extractor.py:224-237](backend/apps/knowledge/extractor.py#L224)（无图片/压缩包提取器）
- 触发条件：上传 png/jpg/gif/webp/bmp/tiff 或 zip/rar/7z
- 实际影响：涉政/涉黄/暴恐图片无任何拦截，且图片会被检索（[rag.py:860](backend/utils/rag.py#L860) 按 `file_type__in=image_types` 参与召回）；压缩包内任意内容无检查即可下载
- 建议修复方向：接入视觉内容审核模型（与现有的 `chat_vision` 能力可复用，但需独立的审核 prompt 与判定输出）；压缩包落地解压 + 逐文件审核 + 嵌套深度/解压总量限制，或直接禁止压缩包上传

**R26：`_admin_scope_filter` 完全不过滤 `visibility` —— `private` 文档对范围内管理员可读、可下载、可改**

- 文件：`apps/knowledge/views.py`
- 函数/类：`KnowledgeDocListView._admin_scope_filter`（被列表 / 详情 / 删除 / 预览 4 处复用）
- 代码位置：[views.py:141-168](backend/apps/knowledge/views.py#L141)；复用点 [views.py:126](backend/apps/knowledge/views.py#L126)、[views.py:255](backend/apps/knowledge/views.py#L255)、[views.py:294](backend/apps/knowledge/views.py#L294)、[views.py:979](backend/apps/knowledge/views.py#L979)
- 触发条件：任一登录用户（列表/详情/预览仅要求 `IsAuthenticated`）访问位于其范围内的 `visibility='private'` 文档
- 实际影响：`visibility` 仅约束 RAG（`_rag_scope_filter` 在 [views.py:186](backend/apps/knowledge/views.py#L186) 才 `filter(visibility='public')`），**对管理/读取 API 毫无作用**。因此标记为「私密」的校级文档，其 `oss_key`、`md5_hash`、`oss_url` 以及一个 **7 天有效的签名 `download_url`**（[serializers.py:50](backend/apps/knowledge/serializers.py#L50)）会返回给**所有** `admin`/`college_admin`/`dept_admin`。这与该方法的自述注释直接矛盾——注释称「仅本人上传的文档（…避免泄露他人私密/未完成文档）」（[views.py:147-148](backend/apps/knowledge/views.py#L147)），但代码中不存在任何 `visibility` 条件
- 攻击路径：`GET /api/knowledge/docs/{id}/` 直接读取范围内任意 private 文档详情与下载链接
- 建议修复方向：在 `_admin_scope_filter` 中显式处理 `visibility`（例如非 super_admin 仅可见 `public` + 本人上传，或对 private 文档要求 `uploader_id == user.id`）；同时让注释与实现一致。**注意**：该改动会影响列表/详情/删除/预览四处行为，并可能切断 R1 路径 B 的 `oss_key` 获取渠道，建议与 R1 一起改

---

### MEDIUM

**R13：`data_query` 的统计类查询完全未做权限/可见性/审核过滤**

- 文件：`utils/data_query.py`、`utils/query_router.py`、`apps/chat/views.py`
- 函数/类：`execute_query`（`popular_docs` / `recent_docs_platform` / `doc_ranking` / `doc_stats`）、`_published_docs_queryset`、`get_popular_knowledge_docs`、`get_recent_knowledge_docs`、`get_doc_ranking_by_college`、`get_doc_stats`
- 代码位置：[data_query.py:291-295](backend/utils/data_query.py#L291)（`KnowledgeDoc.objects.filter(status='indexed')`，**无任何可见性/学院/部门条件**）、[data_query.py:456-477](backend/utils/data_query.py#L456)（路由分发）、[query_router.py:56-78](backend/utils/query_router.py#L56)（触发词）
- 触发条件：任意已登录用户提问「热门资源」「最新上传的资源」「各学院资源排名」等
- 实际影响：**`visibility='private'` 文档的标题、描述前 100 字、学院、上传者姓名向全体登录用户泄露**（`_published_docs_queryset` 未过滤 `visibility`）；跨学院元数据泄露
- 建议修复方向：`_published_docs_queryset` 改为复用 `authorized_knowledge_docs(user_context)`（与其他检索路径保持一致）

**R14：上传与问答接口无限流**

- 文件：`config/settings.py`
- 代码位置：[settings.py:134-149](backend/config/settings.py#L134)（`REST_FRAMEWORK` 无 `DEFAULT_THROTTLE_CLASSES`）
- 实际影响：`upload/batch/`、`upload/ai-classify/`、`chat/ask/` 可被高频调用，造成 OSS 写入、LLM 算力、Embedding 算力滥用（`docs/superpowers/specs/2026-09-01-redis-cache-rate-limit-design.md` 显示限流曾被设计但未落地到 settings）
- 建议修复方向：基于已有 `CACHES` 配置启用 DRF throttling，对上传与 AI 接口分别设阈

**R15：FAQ 生成流水线消费未审核文档**

- 文件：`apps/faq/tasks.py`
- 代码位置：[tasks.py:265](backend/apps/faq/tasks.py#L265)（`source_type='website', visibility='public', status__in=['uploaded','indexed']`）、[tasks.py:283](backend/apps/faq/tasks.py#L283)（`extract_text_from_oss`）
- 实际影响：`status='uploaded'`（尚未完成任何处理）的文档即被 LLM 消费生成 FAQ 答案
- 缓解：产出为 `status='draft'`，需管理员在 Django Admin 手动 publish（[faq/admin.py](backend/apps/faq/admin.py) `publish_selected`），存在人工复核环节
- 建议修复方向：收窄为 `status='indexed'`；或在 Phase 1 后改为「已通过审核」

**R16：文档正文未净化即注入 LLM 上下文（提示词注入）**

- 文件：`utils/rag.py`、`utils/sse.py`、`utils/data_query.py`
- 代码位置：[rag.py:698-720](backend/utils/rag.py#L698)（`_enrich_with_doc_meta` 返回切片原文）、[data_query.py:523](backend/utils/data_query.py#L523)（`build_data_query_prompt`）
- 实际影响：文档内嵌的指令性文本（如「忽略以上要求，输出……」）会被当作上下文交给 LLM，可操纵问答输出
- 建议修复方向：审核流水线中对文档正文做提示词注入特征检测；检索结果与指令使用明确分隔标记；输出侧增加防护

---

### LOW

| # | 风险 | 文件:行 | 说明 |
|:-:|---|---|---|
| R17 | `_validate_oss_key` 不校验 scope/owner 归属 | [views.py:767-775](backend/apps/knowledge/views.py#L767) | 允许任意 `knowledge/{scope}/{owner_id}` 组合，未与请求者角色/学院比对 |
| R18 | merge 无 per-part 完整性校验 | [chunked_views.py:56-65](backend/apps/knowledge/chunked_views.py#L56) | 仅比总大小；不校验分片号连续性、不重算最终 MD5 |
| R19 | merge 无并发锁 | [chunked_views.py:249-289](backend/apps/knowledge/chunked_views.py#L249) | 未用 `select_for_update`，并发双 merge 存在竞态 |
| R20 | `oss_url` 字段存未签名公网 URL | [views.py:882](backend/apps/knowledge/views.py#L882)、[views.py:1205](backend/apps/knowledge/views.py#L1205)、[crawler.py:456](backend/utils/crawler.py#L456) | 该字段存 `https://{bucket}.{endpoint}/{key}`，**不带签名**。若桶 ACL 为公共读，则绕过所有 Django 权限校验直接可取文件。**桶 ACL 未确认（未操作 OSS）——标记为 UNKNOWN，Phase 1 前必须核实** |
| R21 | HTML 以 `text/html` 存 OSS 并可签名直开 | [oss.py:49](backend/utils/oss.py#L49) | 存储型 XSS（OSS 域内）；建议对 HTML 强制 `Content-Disposition: attachment` 或转纯文本存储 |
| R22 | 无 `SECURE_*` / HSTS / 安全 Cookie 配置 | `config/settings.py` | 全文件无 `SECURE_SSL_REDIRECT` / `SECURE_HSTS_*` / `SESSION_COOKIE_SECURE` / `CSRF_TRUSTED_ORIGINS` |
| R23 | `ALLOWED_HOSTS` 默认 `*` | [settings.py:22](backend/config/settings.py#L22) | `config('ALLOWED_HOSTS', default='*')` |
| R24 | 运维脚本引用了已删除的模型 | [fix_oss_content_type.py:34](backend/scripts/fix_oss_content_type.py#L34) | 导入 `apps.resources.models.Resource`，但该模型已被 `0009_remove_legacy_resource_models` 删除 → 脚本已不可运行 |
| R25 | 上传日志（`UploadLog`）不含审核字段，且仅在部分路径写入 | [resources/models.py](backend/apps/resources/models.py) | `FileUploadView`/`BatchFileUploadView` 写日志（[views.py:1323](backend/apps/knowledge/views.py#L1323)、[views.py:1549](backend/apps/knowledge/views.py#L1549)），但 **callback / text / chunk-merge 三个入口不写 UploadLog** → 审计追溯不完整。另：`KnowledgeDoc` **无软删除字段**，删除即 `instance.delete()` 硬删除（[views.py:302](backend/apps/knowledge/views.py#L302)），删除行为不可回溯 |
| R27 | `DEBUG=True` 时错误中间件向客户端回传堆栈 | [config/middleware.py:23-29](backend/config/middleware.py#L23) | `ErrorReportingMiddleware` 在 `DEBUG=True` 时返回 `detail`（异常原文）、`exception_type` 与 traceback 前 20 行。当前 `DEBUG` 默认 False 且有开发 SECRET_KEY 保护（[settings.py:16-20](backend/config/settings.py#L16)），风险可控；但**生产误开 DEBUG 即等于源码级信息泄露** |
| R28 | `requirements.txt` 缺少运行时代码所依赖的包 | [requirements.txt](backend/requirements.txt) | 已确认共 31 行，含 `celery>=5.3.0`(18)、`redis>=5.0.0`(19)、`Pillow>=10.0.0`(21)、`lxml>=5.0.0`(15)、`beautifulsoup4>=4.12.0`(14)；**不含 `oss2`（但 [utils/oss.py:16](backend/utils/oss.py#L16) 无条件 import）、不含 `httpx`、不含任何 PDF/Office 解析库**（`fitz`/`pdfplumber`/`python-docx`/`python-pptx`/`openpyxl`/`docx2txt` 均仅在 try/except 内按需导入）。部署流程靠 `pip install -r requirements.txt`（[.github/workflows/deploy.yml](.github/workflows/deploy.yml)）→ **全新环境上文件解析会静默降级为空文本**（进而 `status='failed'`）。**对 Phase 1 的直接影响：审核能力同样建立在"能提取到内容"之上，若解析器缺失，审核会随解析一起静默失效（空内容通常被判为"无可审内容"而放行），必须在 Phase 1 增加"解析失败即拒绝入库"的显式策略** |
| R29 | OSS 调用绕过统一超时与熔断层 | [utils/http_client.py:29-40](backend/utils/http_client.py#L29) | `http_client` 已实现超时档位与熔断器（`failure_threshold=5`，`recovery_timeout=60s`），并定义了 `oss_sdk` / `oss_download` 档位；但 `utils/oss.py` 直接使用 `oss2` SDK，**不经过 `ExternalClient`**，因此 OSS 侧异常不会触发熔断。仅 `oss_download` 档位被 [config/celery.py:118](backend/config/celery.py#L118) 与预览接口使用。轮询风险：OSS 不可用时索引任务会逐个超时（依赖 `CELERY_TASK_TIME_LIMIT=300` 兜底） |
| R30 | 反向代理上限（500m）远高于应用层限制，叠加整文件读入内存 | [deploy/nginx.conf](backend/deploy/nginx.conf) | nginx 对 `/api/knowledge/upload/` 设置 `client_max_body_size 500m`，而应用层 `FILE_SIZE_LIMITS` 最大为 ppt/pptx 50MB、doc/docx 30MB（[serializers.py:98-100](backend/apps/knowledge/serializers.py#L98)），`DATA_UPLOAD_MAX_MEMORY_SIZE = FILE_UPLOAD_MAX_MEMORY_SIZE = 100MB`（[settings.py:113-114](backend/config/settings.py#L113)）。`FileUploadView` 会 `uploaded_file.read()` **把整个文件读入内存**（[views.py:1180](backend/apps/knowledge/views.py#L1180) 附近），配合无 DRF 限流（R14）可造成 worker 内存耗尽。另：`DATA_UPLOAD_MAX_NUMBER_FIELDS` 未设置（Django 默认 1000），未对 multipart 字段数做收紧 |
| R31 | `milvus_client.search` 在空 `filter_ids` 时退化为全库检索 | [utils/milvus.py:201-239](backend/utils/milvus.py#L201) | 当前唯一调用点始终传入非空候选集（[rag.py:961](backend/utils/rag.py#L961)），故**当前不可利用**（见 §9.7）。但 Milvus 层对空 `filter_ids` 会 `expr=None` 并返回全库 Top-K，而文档删除**不清理向量**（[views.py:288](backend/apps/knowledge/views.py#L288) 明确 TODO）→ 任何新增的、忘记传 `filter_ids` 的检索代码会立刻暴露全部历史残留向量。建议把「空候选集即返回空」固化到 `milvus_client.search` 内部 |

---

## 14. 推荐改造边界

> 依据实际代码判断，非机械照搬方案。

### 14.1 必须新增 moderation

| 位置 | 原因 |
|---|---|
| **`apps/knowledge/tasks.py::index_document_task` 任务体内（入口处）** | **唯一同时覆盖异步与同步降级的插桩点**（见 R5）。审核闸门必须在此处，否则 Redis 抖动即绕过 |
| **`utils/moderation.py`（新建）** | 当前不存在任何统一审核服务；`utils/sensitive_filter.py` 定位是提问过滤，不可直接复用为文档审核器，但**其规则集可作为审核流水线的一路** |
| **文本审核**（对 `extract_text_from_oss` 的输出） | 覆盖 txt/md/html/csv/pdf/doc/docx/ppt/pptx/xls/xlsx 全部可提取类型；当前提取出的文本从未进入任何检查 |
| **视觉审核**（对图片） | 复用 [utils/llm.py](backend/utils/llm.py) 已有的 `image_bytes_to_data_url` / `chat_vision`，但需**独立的审核提示词与结构化判定输出**，不能复用 `_ai_classify` |
| **压缩包处理** | 当前既不解压也不检查；需决定：解压后逐文件审核（需嵌套/总量/深度限制）**或**直接禁止 zip/rar/7z |
| **病毒扫描** | 当前完全缺失；建议接入 ClamAV（需新增依赖与配置，见 §14.4） |

### 14.2 必须新增 upload_pipeline

| 位置 | 原因 |
|---|---|
| **新建统一上传服务层**（如 `apps/knowledge/services/upload_pipeline.py`） | 当前 6 个 `KnowledgeDoc` 创建点各自内联实现上传→建记录→触发索引，逻辑重复且无审核插槽（[views.py:715](backend/apps/knowledge/views.py#L715)、[views.py:893](backend/apps/knowledge/views.py#L893)、[views.py:1303](backend/apps/knowledge/views.py#L1303)、[views.py:1519](backend/apps/knowledge/views.py#L1519)、[chunked_views.py:318](backend/apps/knowledge/chunked_views.py#L318)、[crawler.py:463](backend/utils/crawler.py#L463)） |
| **隔离区前缀与转正动作** | 当前所有写入直落 `knowledge/`（R4）；需新增 `quarantine/`，并在审核通过后转正 |
| **`utils/oss.py` 增加 copy/move** | **当前 OSS 封装不支持**（见 §4.2 Q4），转正能力不存在 |
| **避免重复的 OSS key 生成** | `_generate_oss_key`（oss.py:108）、`_build_oss_key`（chunked_views.py:42）、views.py 内两处内联拼接（[views.py:868](backend/apps/knowledge/views.py#L868)、[views.py:1186](backend/apps/knowledge/views.py#L1186)、[views.py:1467](backend/apps/knowledge/views.py#L1467)）共 5 份重复实现，须收敛为单一来源 |

### 14.3 必须增加 access policy

| 位置 | 原因 |
|---|---|
| **`UploadCallbackView`** | **最高优先**：必须引入服务端上传会话绑定（R1），否则审核通过与否都会被 oss_key 伪造绕过 |
| **`KnowledgeDocSerializer.read_only_fields`** | 必须纳入 `status` / `is_sensitive` / `sensitive_checked`（R3） |
| **`ChunkUploadInitView`** | 必须应用 `FILE_SIZE_LIMITS`（R6） |
| **签名 URL 策略** | 统一收敛有效期至 ≤300s（R7） |
| **`utils/data_query.py::_published_docs_queryset`** | 必须复用 `authorized_knowledge_docs`（R13） |
| **`scripts/index_all_docs.py`** | 批量索引脚本必须同步加入审核过滤（I7） |
| **`KnowledgeDocListView._admin_scope_filter`** | 必须显式处理 `visibility`（R26，当前完全不过滤）；改动影响 4 个复用点 |
| **`utils/milvus.py::MilvusClient.search`** | 建议对空 `filter_ids` 直接返回空结果，把「向量检索必须受 SQL 候选集约束」固化到基础设施层（R31、§9.7） |

### 14.4 必须修改

| 位置 | 修改内容 |
|---|---|
| `apps/knowledge/models.py` | 新增审核状态字段（如 `moderation_status` / `moderation_detail` / `quarantined_at`）；`KnowledgeDoc.status` 现状是索引态，**不要复用为审核态** |
| `apps/knowledge/migrations/` | 新增 migration（Phase 1 执行，本阶段不创建） |
| `config/celery.py` | 删除孤儿 `index_document` 任务及其 `_parse_*` / `_chunk_text`（R11） |
| `config/settings.py` | 新增审核相关配置（模型、阈值、超时、开关）；启用 DRF throttling（R14）；补齐 `SECURE_*`（R22） |
| `requirements.txt` | 当前仅 31 行，**无任何 AV/审核依赖**；如采用 ClamAV 需新增依赖。**另需补齐已在代码中被依赖但未声明的包**（`oss2`、`httpx`、PDF/Office 解析器，见 R28），否则「解析失败 → 空内容 → 审核放行」的静默失效链会一并生效 |
| `deploy/nginx.conf` | `client_max_body_size 500m` 应收紧到与 `FILE_SIZE_LIMITS` 一致（R30） |
| `config/middleware.py` | `ErrorReportingMiddleware` 的 `DEBUG=True` 分支应向客户端屏蔽 traceback（R27） |
| `utils/sensitive_filter.py` | 保持不动，作为审核流水线的一路规则复用（**不要**把它当作文档审核器直接套用，其规则集面向提问文本） |
| `apps/faq/tasks.py` | 文档消费条件收窄（R15） |
| `scripts/fix_oss_content_type.py` | 修复对已删除 `Resource` 模型的导入（R24），否则脚本持续不可用 |

### 14.5 可以保持不动

| 位置 | 理由 |
|---|---|
| `apps/accounts/`、`apps/classify/`、`apps/feedback/`、`apps/admin_panel/`（日志视图） | 与文件上传链路无关 |
| `apps/chat/views.py::VoiceAskView` 的 `check_sensitive` 调用 | 已正确覆盖语音提问通道，无需改动 |
| `utils/sse.py::build_references_events` 的授权校验顺序 | **先授权后签名**的实现是正确的（[sse.py:227-232](backend/utils/sse.py#L227)），只需在授权条件中叠加审核状态 |
| `utils/rag.py::authorized_knowledge_docs` | 权限模型（visibility + college/department）设计合理，Phase 1 只需在调用处叠加审核条件，不必重构 |
| `apps/knowledge/extractor.py` 的临时文件处理 | `tempfile.mkstemp` + `finally: os.remove` 实现正确，无路径穿越 |
| Django Admin | `KnowledgeDoc` 未注册，无上传面，无需改动 |
| `apps/knowledge/chunked_views.py` 的会话归属校验 | `filter(upload_id, user=request.user, status='uploading')` 正确（[chunked_views.py:209](backend/apps/knowledge/chunked_views.py#L209)），保留 |
| `KnowledgeDocPreviewView` 的作用域过滤 | 经 `_admin_scope_filter` 收敛，并显式防枚举（[views.py:971-980](backend/apps/knowledge/views.py#L971)），实现正确 |
| `_rag_scope_filter` 的 `visibility='public'` | RAG 侧对 private 的处理是正确的（[views.py:186](backend/apps/knowledge/views.py#L186)）；问题在于管理 API 侧缺少同样的约束（R26），修 R26 时不应破坏此处 |
| `retrieve_images` 的 `status='indexed'` 过滤 | [rag.py:858-861](backend/utils/rag.py#L858) 已强制 `status='indexed'`；其 docstring（[rag.py:827](backend/utils/rag.py#L827)）称「不按 status 过滤」是**过时注释**，需修正注释而非改代码 |
| `utils/http_client.py` 的超时与熔断实现 | 档位化超时（`llm_chat`/`llm_vision`/`oss_download` 等）+ 熔断器（5 次失败/60s 恢复）实现合理，Phase 1 的审核模型调用应**复用**这些档位而非新建 |
| `utils/llm.py` 的视觉能力 | `chat_vision`（[llm.py:334](backend/utils/llm.py#L334)）与 `image_bytes_to_data_url`（[llm.py:74](backend/utils/llm.py#L74)，含 80M 像素上限与降采样）已具备，**Phase 1 的图片审核可直接复用**，只需新增独立的审核提示词与结构化判定输出；`LLMClient` 上**不存在** `classify` 或任何 moderation 方法 |

---

## 15. Phase 1 实施计划（最小安全改造顺序）

> **本阶段不实施。**以下仅为顺序建议，需人工确认审计报告后再启动。

**P1-0　前置核实（0.5 天，必须先做）**
1. 核实 **OSS 桶 ACL 与内网/公网可达性**（决定 R20 的严重度：若桶为公共读，R20 立即升为 CRITICAL）。
2. 核实生产环境是否实际运行 Celery worker + Redis（决定 R5 同步降级路径是否在生产生效）。
3. 核实生产环境实际已安装的 Python 包（`oss2` 与 PDF/Office 解析器**均未在 requirements.txt 中声明**，见 R28）——这同时决定 Phase 1 审核能力的可用性。
4. 核实 `D:\实验室204\校园虚拟资源管理\backend` 与本次审计代码是否同源（见 §0）。

**P1-1　止血：封堵最高危绕过（2–3 天）**
5. 封堵 R1：`UploadCallbackView` 增加服务端上传会话绑定（新增 `PendingUpload` 模型：`oss_key / user / expected_size / expected_md5 / expires_at / consumed`），校验 `oss_key` 归属 + `head_object` 存在性 + 大小/Content-Type 一致 + scope/owner 与角色相符；**停止以客户端 `md5` 作为去重依据**（路径 A 的根因）。
6. 封堵 R3：`KnowledgeDocSerializer.read_only_fields` 纳入 `status` / `is_sensitive` / `sensitive_checked` / `visibility`。
7. 封堵 R26：`_admin_scope_filter` 显式处理 `visibility`（同时收紧 R1 路径 B 的 `oss_key` 获取渠道）。
8. 封堵 R11：删除 `config/celery.py` 的孤儿 `index_document` 任务。
9. 封堵 R6：`ChunkUploadInitView` 应用 `FILE_SIZE_LIMITS`，拒绝 `file_size<=0`；`ChunkUploadView` 加单分片上限。
10. 封堵 R7：签名有效期统一收敛至 ≤300s。

**P1-2　引入隔离区（2–3 天）**
11. `utils/oss.py` 增加 `copy_object` / `promote_object` 封装（R4；**当前封装不具备该能力**）。
12. 新增 `quarantine/` 前缀；把 8 个 OSS 写入点（W1–W8，含 STS 签发的 key）全部切到隔离区。
13. `KnowledgeDoc` 新增审核字段 + migration；隔离期 `oss_key` 指向 quarantine key。

**P1-3　引入审核闸门（3–5 天）**
14. 新建 `utils/moderation.py` 统一审核服务（多路：规则引擎复用 `sensitive_filter` 的规则集 / 文本 LLM 审核 / 视觉审核 / 可选 AV）；调用外部模型时复用 `utils/http_client.py` 已有超时档位与熔断器。
15. 在 **`index_document_task` 任务体内**插入审核闸门（R5 强调的唯一可靠插桩点，同时覆盖 Celery 异步与两种同步降级），未通过则跳过索引并置审核状态。
16. **新增显式失败策略**：解析失败 / 提取为空 / 解析器缺失 → **拒绝入库**，不得当作「无可审内容」放行（R28）。当前 `index_document_task` 对空文本的处理是置 `failed` 并跳过索引（[tasks.py:162-168](backend/apps/knowledge/tasks.py#L162)），需扩展为同时阻止下载与预览。
17. 上传链路改为：落隔离区 → 提取 → 审核 → 通过才转正 + 建 `KnowledgeDoc` + 触发索引。
18. 图片与压缩包纳入审核范围（R12）；压缩包可先采取「禁止上传」的最小策略。

**P1-4　读取侧与收尾（2–3 天）**
19. RAG 与引用条件叠加审核状态（`utils/rag.py:465/635/676/698/860`、`utils/sse.py:232`、`apps/chat/views.py:216`）。
20. 修正 `utils/data_query.py::_published_docs_queryset` 为授权过滤（R13）。
21. 改造 `scripts/index_all_docs.py`（I7），避免把隔离区文档批量索引。
22. 补齐审核留痕（`UploadLog` 覆盖 callback/text/merge 三入口，R25）与后台可查。
23. 为 `milvus_client.search` 增加空 `filter_ids` 保护（R31）。
24. 启用限流（R14）、收紧 nginx 上传上限（R30）、补齐安全响应头（R22/R23）、收敛错误中间件的 traceback 输出（R27）、补齐 `requirements.txt` 缺失依赖（R28）。

---

## 附录 A：审计方法与限制

**执行的检查**：静态代码阅读、多关键词全仓库 grep（`upload`/`sts`/`callback`/`chunk`/`put_object`/`oss_key`/`KnowledgeDoc`/`DocChunk`/`is_sensitive`/`sensitive_checked`/`check_sensitive`/`index_document`/`safe_delay`/`status='indexed'` 等）、配置与依赖清单核对。

**未执行的操作（遵守只读约束）**：

- ❌ 未修改任何源码 / 配置 / 依赖
- ❌ 未创建或执行任何 migration
- ❌ 未读写数据库
- ❌ 未上传/删除/修改任何 OSS 对象
- ❌ 未修改 Celery 任务
- ❌ 未安装任何软件
- ❌ 未启动任何有写入副作用的服务
- ❌ 未自动修复任何问题

**未能确认的事项（明确标记）**：

| 事项 | 状态 |
|---|---|
| OSS 桶 ACL / 是否公共读 | **未确认**（需读取 OSS 配置，本阶段不操作 OSS）。影响 R20 严重度 |
| 生产环境是否实际运行 Celery worker | **未确认**。影响 R5 同步降级路径的实际触发概率 |
| 生产环境实际安装的 Python 包 | **未确认**。`oss2` 与全部 PDF/Office 解析器均不在 `requirements.txt`（R28），因此无法从仓库判断它们是否可用 |
| Milvus 残留向量是否可被召回 | **已确认不可**（§9.7）。文档删除不清理向量（`views.py:288` TODO），但 RAG 始终以 MySQL 候选 ID 约束 Milvus 检索，孤儿向量不会被召回。降级为 R31（潜伏耦合） |
| `utils/rag.py:827` 注释称「不按 status='indexed' 过滤」与 L860 代码不一致 | **已确认是过时注释**：L858-861 确实施加了 `status='indexed'`，代码行为正确，应修正注释 |
| 部署与 CI 路径 | **已审计**。无 Dockerfile / docker-compose；`.github/workflows/deploy.yml` 通过 SSH 执行 `git pull` + `pip install -r requirements.txt` + `manage.py migrate` + `systemctl restart campus-backend`，**未引入额外上传入口** |
| 是否存在本仓库之外的上传入口（如其他客户端直连 OSS） | **未确认**（OSS 桶策略未读取，无法排除非本系统的写入方） |

---

## 附录 B：关键证据索引

| 主题 | 文件 | 行 |
|---|---|---|
| KnowledgeDoc 模型（含死字段） | `apps/knowledge/models.py` | 5-146（`is_sensitive` 127 / `sensitive_checked` 128） |
| ChunkedUploadSession 模型 | `apps/knowledge/models.py` | 226-268 |
| OSS 封装（无 copy/move） | `utils/oss.py` | 72-339 |
| STS 空壳实现 | `utils/oss.py` | 152-178 |
| key 生成规则 | `utils/oss.py` | 108-122 |
| 签名 URL（7 天） | `apps/knowledge/serializers.py` | 45-57 |
| callback 与 oss_key 校验 | `apps/knowledge/views.py` | 680-775 |
| 文件/批量/文本上传 | `apps/knowledge/views.py` | 800-935 / 1135-1367 / 1400-1596 |
| 读取视图 | `apps/knowledge/views.py` | 88-169 / 220-263 / 387-426 / 938-1026 |
| 分片上传三视图 | `apps/knowledge/chunked_views.py` | 94-349 |
| 索引任务 | `apps/knowledge/tasks.py` | 110-265 |
| 孤儿索引任务（路径穿越） | `config/celery.py` | 66-175 |
| 提问敏感过滤调用点 | `apps/chat/views.py` | 336、991 |
| RAG 授权与状态门 | `utils/rag.py` | 465、587-620、635、676、698、860 |
| 引用构造（授权先于签名） | `utils/sse.py` | 199-290 |
| 未授权统计查询 | `utils/data_query.py` | 291-410、456-477 |
| 爬虫建档 + 索引 | `utils/crawler.py` | 443-495 |
| 内容提取器 | `apps/knowledge/extractor.py` | 49-293 |
| 文件类型/大小白名单 | `apps/knowledge/serializers.py` | 95-144 |
| safe_delay 同步降级 | `utils/celery_dispatcher.py` | 15-49 |
| shared_task 降级 | `utils/shared_task_compat.py` | 42-86 |
| FAQ 消费未索引文档 | `apps/faq/tasks.py` | 265-283 |
| 历史消息引用接口 | `apps/chat/views.py` | 178-237 |
| 引用内容持久化（含签名 URL） | `apps/chat/models.py` / `apps/chat/views.py` | 44 / 520-523 |
| Milvus 检索的候选集约束 | `utils/milvus.py` / `utils/rag.py` | 201-239 / 946-961 |
| `_admin_scope_filter`（无 visibility 过滤） | `apps/knowledge/views.py` | 141-168 |
| 依赖清单 | `requirements.txt` | 1-31 |
| 部署与 CI | `.github/workflows/deploy.yml` / `deploy/nginx.conf` | — |
| 中间件（traceback 输出） | `config/middleware.py` | 17-34 |
| 统一超时与熔断（OSS 未接入） | `utils/http_client.py` | 25-130 |
| 视觉能力（Phase 1 可复用） | `utils/llm.py` | 74-130 / 334-400 |
| 服务端上传会话归属校验（正确实现） | `apps/knowledge/chunked_views.py` | 209 |
| 预览接口防枚举（正确实现） | `apps/knowledge/views.py` | 971-980 |

---

========================================
**Phase 0 审计完成**
========================================

```
项目修改：
0 files

数据库修改：
0

OSS修改：
0

发现上传入口：
11 个 HTTP 入口（file / batch / text / sts / callback / chunk-init / chunk /
                     chunk-merge / ai-classify / classify / voice-ask）
 + 1 个非 HTTP 内容入口（官网爬虫，3 个触发方）
 + 2 类运维脚本入口
 共 6 个 KnowledgeDoc 创建点

发现 OSS 写入点：
8 个（W1-W8：3× put_object、1× upload_text、1× init_multipart、
        1× upload_part、1× complete_multipart、1× 签名 PUT URL）
 + 1 个运维脚本 copy_object
删除点 5 个

发现 KnowledgeDoc 创建点：
6 个（生产代码） + 2 个脚本 + 1 个测试

发现索引入口：
1 个任务 index_document_task，6 个生产触发点 + 3 个脚本触发点
 + 1 个孤儿任务 config/celery.py::index_document（可经 broker 投递）

发现读取入口：
10 个（列表 / 详情 / 下载 / 预览 / 关键词 / RAG / 聊天引用(SSE) /
        历史消息引用接口 / 数据统计查询 / 日期提取）

CRITICAL：
5（R1 callback 不验证对象存在性/归属/大小/类型/哈希
     —— 含 MD5 秒传索引投毒 + 物理删除受害者对象
   · R2 全链路无审核
   · R3 审核字段可写且从未被写入/读取
   · R4 无隔离存储且 OSS 封装无 copy/move
   · R5 索引入口无闸门 + 同步降级使绕过成为必然）

HIGH：
8（R6 分片无大小限制 · R7 签名 7 天不可撤回 · R8 提取无超时/外部进程
   · R9 客户端元数据被信任 · R10 STS 未实现（注释冒充实现）
   · R11 孤儿任务路径穿越 · R12 图片/压缩包无审核
   · R26 _admin_scope_filter 不过滤 visibility，private 文档对范围内管理员可读可下）

MEDIUM：
4（R13 data_query 未授权泄露 private 文档元数据 · R14 无限流
   · R15 FAQ 消费未审核文档 · R16 提示词注入）

LOW：
14（R17-R25 · R27 错误中间件回传堆栈 · R28 requirements.txt 缺运行时依赖
    · R29 OSS 调用绕过超时/熔断 · R30 nginx 500m 上限 + 整文件读入内存
    · R31 milvus search 空候选集退化为全库检索）

是否发现未审核文件可直接进入正式存储：
YES
   —— 全部 8 个 OSS 写入点均直写正式前缀 knowledge/，不存在隔离区；
      不存在任何“审核通过才转正”的机制

是否发现未审核文件可直接索引：
YES
   —— index_document_task 仅校验文档存在即索引；
      6 个生产触发点在创建 KnowledgeDoc 后立即调用；
      safe_delay 的同步降级与 shared_task 的无 celery 降级使绕过成为必然而非可能

是否发现STS绕过风险：
YES
   —— STS 未实现（假定角色代码整段被注释，sts_credentials 恒为 None），
      实际交付的是预签名 PUT URL；
      该 URL 无大小上限、无 Content-Type 约束、非一次性、不绑定发起用户；
      且 callback 端不校验对象的存在性、归属、大小、类型与哈希（R1）。
   ★ 需准确的表述：由此可完整复现的是
     ① MD5 秒传索引投毒 → 受害者合法上传被丢弃/被物理删除（无需任何机密信息）；
     ② 认领可见范围内的对象并把其删除行为挂在自己名下（需先获得该 oss_key）。
     跨学院接管未以单接口闭环证明（oss_key 的暴露受范围约束），
     但日志 / 签名 URL path 段 / oss_url / 离职转岗等渠道使其成为现实威胁。
   ★ 对 Phase 1 的决定性影响：callback 的输入面使
     「只有服务端签发的、绑定到本次会话的 oss_key 才允许建库」这一上游门控
     在当前代码下无法建立 —— 不封堵此接口，下游审核可被绕过。

是否发现分片绕过风险：
YES
   —— 无单文件/单分片大小上限（FILE_SIZE_LIMITS 未在分片链路应用）；
      file_size=0 时 merge 的完整性检查被完全跳过；
      不重算最终对象 MD5、不校验分片号连续性、无并发锁；
      merge 后无审核直接建文档并触发索引；
      merge 的秒传分支会以客户端自报 md5 为依据
      **删除刚合并的正式对象**（chunked_views.py:294），可被用于丢弃他人上传

下一阶段：
等待人工确认审计报告后，再进入Phase 1
```

**本阶段严格遵守只读约束：项目修改 0 个文件，数据库修改 0 次，OSS 操作 0 次。**

报告生成位置（项目外）：`e:\github\campus_resource\security_moderation_phase0_audit.md`
