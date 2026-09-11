# P1-2D STEP 4-B 封版后 · 生产环境 E2E 审计报告

- 审计日期：2026-09-11
- 审计对象：`E:\github\campus_resource\backend`
- 审计性质：**只读审计（READ-ONLY）**

**未修改任何**：Python 业务代码 / models / migration / settings / Celery / crawler / RAG / Milvus / FAQ / 前端 / CORS / OSS 逻辑 / ClamAV 逻辑 / moderation 逻辑 / promotion 逻辑 / 历史数据 / 数据库结构 / 依赖。

---

## 一、规则遵守声明

### 1.1 实际执行过的操作（全部只读）

| 类别 | 具体命令 / 动作 |
|---|---|
| Git | `git status --short` `git branch --show-current` `git log --oneline -1` `git diff --check` `git ls-files --eol` `git diff --cached --stat` |
| 配置 | 读取 `settings`（Django 只读加载）；读取 `.env` 的**键名**（未输出任何 secret 值，仅输出长度占位） |
| 网络 | DNS 解析、TCP 连通性探测（只 connect/close） |
| OSS | `get_bucket_info()`、`list_objects()`（只读列举，未 get/put/delete） |
| 依赖 | `importlib.import_module` 逐个探测 |
| 检测链 | 在内存中构造 100% 干净样本，运行真实 `ClamAVAdapter` / `RuleAdapter` / `LLMAdapter` / `aggregate_results`（未写库、未写 OSS） |

### 1.2 未执行的操作

- 未写库、未写 OSS、未派发任何任务、未运行任何 migration。
- 未执行 `git reset` / `checkout` / `stash` / `clean` / 删除任何文件。
- 除本报告文件外，未创建或修改任何文件。

### 1.3 发现问题一律未修复

本文第六节所有问题均**只记录、不修复**，格式为：`问题 → 风险 → 影响范围 → 建议修复阶段`。

---

## 二、Git 状态确认（必列项）

| 项目 | 值 |
|---|---|
| 当前 branch | `phase-1-1-security-stopgap` |
| HEAD | `8cd98a7 Merge branch 'main' into phase-1-1-security-stopgap` |
| working tree | 13 个已修改 + 8 个未跟踪 |
| 暂存区 | 空（`git diff --cached` 无输出） |
| `git diff --check` | 干净（无空白错误、无冲突标记） |
| 是否发现 STEP 4-B 之外的意外修改 | **未发现** |

### 2.1 已修改（13）

```
 M backend/apps/knowledge/chunked_views.py
 M backend/apps/knowledge/models.py
 M backend/apps/knowledge/moderation.py
 M backend/apps/knowledge/serializers.py
 M backend/apps/knowledge/tasks.py
 M backend/apps/knowledge/test_moderation_service.py
 M backend/apps/knowledge/tests.py
 M backend/apps/knowledge/views.py
 M backend/config/celery.py
 M backend/config/settings.py
 M backend/utils/data_query.py
 M backend/utils/oss.py
 M backend/utils/sse.py
 M code-changes/2026-09-10.md
```

### 2.2 未跟踪（8）

```
?? backend/apps/knowledge/migrations/0009_uploadintent_chunkeduploadsession_intent_and_more.py
?? backend/apps/knowledge/promotion.py
?? backend/apps/knowledge/test_promotion_flow.py
?? backend/apps/knowledge/test_upload_intent.py
?? backend/apps/knowledge/upload_intent.py
?? backend/apps/knowledge/upload_service.py
?? database-changes/chunked_upload_session.md
?? database-changes/upload_intent.md
```

### 2.3 归属判定

上述 13 + 8 项**全部可归因于 STEP 3 / STEP 4-A / STEP 4-A.2 / STEP 4-B**：

- STEP 3 / 4-A：`upload_intent.py`、`upload_service.py`、`chunked_views.py`、`views.py`、`utils/oss.py`、`migrations/0009_*`、`database-changes/*`
- STEP 4-A.2：`utils/data_query.py`、`utils/sse.py`、`serializers.py`
- STEP 4-B：`promotion.py`、`moderation.py`、`tasks.py`、`config/celery.py`、`config/settings.py`、`test_promotion_flow.py`、`tests.py`、`test_moderation_service.py`、`code-changes/2026-09-10.md`

**结论：未发现任何不属于上述阶段的意外改动，working tree 完好，STEP 3 / 4-A / 4-A.2 / 4-B 的未提交修改完整保留。**

---

## 三、真实运行环境审计

> **前置结论（重要）**：本次审计所在机器是一台 **Windows 开发机**，**不是生产运行环境**。
> 生产库（阿里云 RDS，VPC 内网地址）在本机无法解析，ClamAV / Celery / Redis / Milvus / Django 服务在本机均不存在。
> 因此「本机探测不到」**不能**直接等同于「生产不可用」。第 3.1 节严格区分二者，请按标注阅读。

### 3.1 组件可用性矩阵

| 组件 | 本机实测 | 判定 | 证据 |
|---|---|---|---|
| Django settings | 可加载 | 仅本机 | `DJANGO_SETTINGS_MODULE=config.settings` 加载成功；`DEBUG=True`、`ALLOWED_HOSTS` 含 `'*'` |
| Django HTTP 服务 | 未运行 | 仅本机 | `8000` 端口关闭；`80` 端口为 btPanel `httpd.exe` |
| 生产 MySQL（RDS） | **无法审计** | **生产侧不可达** | `rm-bp18t1fwh5xb9cj7hro.mysql.rds.aliyuncs.com` DNS `gaierror`（Errno 11001），TCP 同样失败 |
| 本机 MySQL | 在跑但非生产 | 仅本机 | `3306` 由 btPanel `mysqld.exe`（pid 8888）占用；root 无密码连接被拒（`1045`）。**与配置中的生产库不是同一个实例** |
| OSS Bucket | **可达** | **生产侧已确认** | `get_bucket_info()` → `200`，`471ms`；见 3.2 |
| ClamAV / clamd | 不可达 | 仅本机 | `127.0.0.1:3310` 连接被拒；实测 `daemon_unreachable` |
| Redis / Broker | 不可达 | 仅本机 | `CELERY_BROKER_URL=redis://127.0.0.1:6379/0`；`6379` 端口关闭；本机无 `redis` / `celery` / `kombu` 模块 |
| Celery worker | 未运行 | 仅本机 | 同上 |
| Celery beat | 未运行 | 仅本机 | 同上 |
| Milvus | 不可用 | 仅本机 | `19530` 端口关闭；无 `pymilvus` |
| ChromaDB（回退） | 不可用 | 仅本机 | 无 `chromadb`；见 P-7 |
| Embedding 服务 | 可达 | 生产侧大概率可用 | 真实 LLM 适配器 HTTP 调用成功 |
| RAG 查询链 | 代码可加载，数据依赖不可达 | 混合 | 见第四节 |

### 3.2 OSS 实测结果（本次审计最有价值的生产侧证据）

```
bucket   = campus-resourse          （注意：拼写为 resourse，非 resource）
endpoint = oss-cn-chengdu.aliyuncs.com
get_bucket_info() -> 200  (471ms)

顶层前缀（delimiter='/'）: ['knowledge/']      <-- 只有这一个
根级对象: []

knowledge/   n=95    truncated=False
quarantine/  n=0     （该前缀完全不存在）
```

`knowledge/` 下 95 个对象的分布：

| 维度 | 分布 |
|---|---|
| 二级段 | `college` 22 / `school` 72 / `website` 1 |
| college_id | `0` 90 / `1` 5 |
| 年-月目录 | 全部为 `2026-09` |
| 扩展名 | docx 19 / md 11 / zip 10 / doc 9 / xlsx 9 / txt 8 / pdf 7 / webp 5 / pptx 5 / png 4 / mp3 4 / mp4 3 / jpg 1 |

**由此可确证的三件事（属生产侧事实，非本机推断）**：

1. **`quarantine/` 命名空间下对象数为 0**，且该前缀根本不存在 —— 说明 STEP 4-A 的隔离区上传在该 bucket 上**从未真实发生过一次**（要么尚未部署到生产，要么部署后无任何上传）。
2. 现存 95 个对象**全部直接躺在 `knowledge/` 正式区**，全部为本阶段之前的**历史对象**，其对应的 `KnowledgeDoc` 不会有 `ContentModeration` 记录。
3. `school/` 72 + `website/` 1 符合爬虫产出形态，这些文档同样没有审核记录。

### 3.3 关键配置实测值

| 配置项 | 实测值 |
|---|---|
| `DEBUG` | `True` |
| `ALLOWED_HOSTS` | 含 `'*'` |
| `CORS_ALLOW_ALL_ORIGINS` | `= DEBUG`（settings.py:131）→ 当前为 **True** |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` |
| `ATOMIC_REQUESTS` | 未设置（默认 `False`） |
| `CELERY_TASK_ALWAYS_EAGER` | 未设置（默认 `False`） |
| `CELERY_TASK_TIME_LIMIT` | `300` |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `240` |
| `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/0` |
| `CLAMAV_ENABLED` / `HOST` / `PORT` / `TIMEOUT` | `True` / `127.0.0.1` / `3310` / `30` |
| `LLM_MODERATION_ENABLED` / `TIMEOUT` | `True` / `60` |
| `VISION_MODERATION_ENABLED` / `TIMEOUT` | `True` / `120` |
| `CONTENT_MODERATION_MAX_ATTEMPTS` | `3` |
| `CONTENT_MODERATION_RETRY_BACKOFF_SECONDS` | `300` |
| `CONTENT_MODERATION_POLICY_VERSION` | `content-moderation-v1` |
| `OSS_BUCKET_NAME` / `OSS_ENDPOINT` | `campus-resourse` / `oss-cn-chengdu.aliyuncs.com` |
| `OSS_ACCESS_KEY_ID` / `_SECRET` | 已设置（值未输出） |

### 3.4 部署形态审计

仓库内**唯一**的启动脚本与部署产物：

**`backend/start.bat`（全仓库唯一启动脚本）**
```bat
set PATH=C:\wwwroot\ffmpeg;%PATH%
cd /d C:\wwwroot\campus_resource\backend
.\venv\Scripts\python.exe manage.py runserver 0.0.0.0:3000
```

**`backend/deploy/nginx.conf`（全仓库唯一部署产物）**
```nginx
upstream django { server 127.0.0.1:8000; keepalive 16; }
server {
    listen 80; server_name localhost;
    location /api/chat/        { proxy_pass http://django; proxy_buffering off; proxy_read_timeout 300s; ... }
    location /api/              { proxy_pass http://django; proxy_read_timeout 60s; }
    location /api/knowledge/upload/ { proxy_pass http://django; client_max_body_size 500m; proxy_read_timeout 120s; }
    location /static/           { alias /path/to/staticfiles/; expires 30d; }
    location /                { proxy_pass http://django; proxy_read_timeout 60s; }
}
```

**未发现**：Dockerfile / docker-compose / systemd unit / supervisor 配置 / gunicorn / uwsgi / Celery worker 或 beat 的启动脚本。

**`scripts/reindex_all.bat`（关键运维脚本，见 P-1）**
```bat
echo [1/4] Clearing old index data...
python manage.py shell --command="...DocChunk.objects.all().delete(); KnowledgeDoc.objects.all().update(status='uploaded')"
echo [2/4] Removing old ChromaDB...
rmdir /s /q chroma_db 2>nul
echo [3/4] Re-indexing all documents...
python manage.py shell --command="...docs=KnowledgeDoc.objects.filter(status='uploaded')...; [index_document_task(d.id) for d in docs]"
echo [4/4] Verifying RAG search...
```

### 3.5 依赖完整性（本机 venv vs `requirements.txt`）

`requirements.txt` **已声明**但本机 venv **缺失**：

| 缺失模块 | requirements 声明 |
|---|---|
| `celery` | `celery>=5.3.0` |
| `redis` | `redis>=5.0.0` |
| `chromadb` / `chroma_hnswlib` | `chromadb==0.6.3` |
| `pymilvus` | 已声明 |
| `fitz`（PyMuPDF） | `PyMuPDF>=1.24.0` |
| `pptx` | `python-pptx>=1.0.0` |
| `openpyxl` | `openpyxl>=3.1.0` |
| `xlrd` | `xlrd==1.2.0` |
| `pdfplumber` / `docx2txt` / `faster_whisper` / `pydub` / `sentence_transformers` / `transformers` / `torch` / `rank_bm25` / `jieba` | 视使用路径 |

本机**已有**：`django 4.2`、`oss2 2.19.1`、`pymysql 2.2.8`、`MySQLdb`、`requests 2.34.2`、`numpy 2.4.6`、`docx`、`PIL`。

**判定：本机 venv 是一份不完整的开发环境（未按 `requirements.txt` 安装），不是生产环境。** 因此第 3.1 节中所有「本机不可用」的结论**不得**直接当作生产故障上报。

---

## 四、E2E 链路逐段真实性核对

目标链路：

```
用户上传 → UploadIntent → quarantine/ → KnowledgeDoc → ContentModeration
       → moderate_document() → approved / confirmed_unchecked
       → promotion → knowledge/ → index_document_task → Milvus → RAG
```

| # | 环节 | 代码是否接通 | 本机可验证 | 生产可验证 |
|---|---|---|---|---|
| 1 | 5 个入口触发审核编排 | 已接通（views.py:869 / 1121 / 1577 / 1787，chunked_views.py:416） | 单元测试覆盖 | 需 DB |
| 2 | `quarantine/` 隔离区写入 | 代码在（`build_quarantine_key`） | — | **OSS 实测为 0 对象，该环节在生产从未跑过** |
| 3 | `moderate_document()` 写审核结论 | 代码在 | 真实适配器实测（见 5.2） | 需 DB |
| 4 | 白名单判定 `approved`/`confirmed_unchecked` | 单一白名单 `INDEXABLE_MODERATION_STATES` | 实测 | 需 DB |
| 5 | `confirmed_unchecked` 人工放行 | **无任何写入方**（见 P-2） | 实测确认 | — |
| 6 | `promote_document()` 提升 | 代码在（`promotion.py`） | 单元测试覆盖 | **从未在生产执行过**（quarantine 为空） |
| 7 | 提升后 `KnowledgeDoc.oss_key` 更新 | 仅在校验 copy 成功后 | 单元测试覆盖 | — |
| 8 | `index_document_task` 审核门 | `check_indexable()` 已接入（tasks.py:147） | 已确认 | 需 DB |
| 9 | Milvus 写入 | 代码在，后端缺失时静默降级 | 否 | 需生产确认 |
| 10 | RAG 读取 | **无审核门**（见 P-3） | 代码审计确认 | 需 DB |

**结论：链路 1–4、6–8 在代码层面确实接通且有测试覆盖；链路 2、6 在生产侧从未真实运行过一次；链路 5 完全缺失；链路 9 存在静默降级；链路 10 缺少审核门。**

---

## 五、fail-closed 状态实际行为

### 5.1 白名单语义确认

`INDEXABLE_MODERATION_STATES = frozenset({'approved', 'confirmed_unchecked'})` —— 白名单语义，任何未列出的状态一律不放行。代码审计确认：

- `is_document_indexable_by_moderation()`（moderation.py）：无记录 → `False`；状态不在白名单 → `False`；`storage_state != promoted` → `False`。
- `promote_document()`（promotion.py）：无记录 → `STATUS_SKIPPED`；状态不在白名单 → `STATUS_SKIPPED`。
- `check_indexable()`（moderation.py）：唯一集中式入口，被 `index_document_task` 调用。

**5 个 fail-closed 状态的实际行为（代码审计 + 单元测试确认）**：

| 状态 | promotion | knowledge/ | index_document_task | Milvus | RAG |
|---|---|---|---|---|---|
| `rejected` | skip | 不进入 | 拒绝（写 `failed`） | 不写入 | 不进新文档 |
| `unavailable` | skip | 不进入 | 拒绝（写 `failed`） | 不写入 | 不进新文档 |
| `error` | skip | 不进入 | 拒绝（写 `failed`） | 不写入 | 不进新文档 |
| `pending` | skip | 不进入 | 拒绝（写 `failed`） | 不写入 | 不进新文档 |
| `scanning` | skip | 不进入 | 拒绝（写 `failed`） | 不写入 | 不进新文档 |

**在写入侧，fail-closed 语义实现正确、无发现的绕过路径，符合 STEP 4-B 的设计目标。**

### 5.2 真实检测器链实测（本机，用真实适配器，不 mock）

对一份 **100% 干净的 `.txt` 样本**运行生产同款检测链：

```
ClamAVAdapter().scan_chunks(...)  ->  status=unavailable
                                      reason=daemon_unreachable
                                      error_message='病毒扫描服务不可达: ConnectionRefusedError'
RuleAdapter().check()             ->  ok / clean
LLMAdapter().check()              ->  ok / clean       （真实 HTTP，调用成功）
aggregate_results(...)            ->  state   = unavailable
                                      verdict = unavailable
                                      service = clamav
```

`unavailable` 不在 `INDEXABLE_MODERATION_STATES` 中 ⇒ **该文档不得提升、不得索引**。

**这正是 fail-closed 设计意图的正确体现**（宁可拦下，绝不误放行）。但随之产生 P-2 描述的可用性问题：一旦 ClamAV 不可用，**每一个**上传都会停在 `unavailable`，而系统没有任何人工放行阀门。

另：`.pdf` 样本在此环境额外因缺 `pdfplumber`/PyMuPDF 而 `unavailable`（属本机依赖缺失，非生产结论）。

### 5.3 向量后端实测

```
pymilvus not installed, falling back to ChromaDB
chromadb not installed
connect()            -> False
ensure_collection()  -> False
search(...)          -> []          <-- 静默返回空列表，不抛异常
```

见 P-7。

---

## 六、问题清单（问题 → 风险 → 影响范围 → 建议修复阶段）

### P-1 CRITICAL ｜ `scripts/reindex_all.bat` 在 STEP 4-B 之后成为不可逆的「知识库毁灭」脚本

- **问题**：该脚本的执行顺序是
  1. `DocChunk.objects.all().delete()` —— 删除**全部**分块
  2. `KnowledgeDoc.objects.all().update(status='uploaded')` —— 把**全部**文档（含已在 RAG 中的 `indexed` 文档）重置为 `uploaded`
  3. `rmdir /s /q chroma_db`
  4. `[index_document_task(d.id) for d in docs]` —— 逐个重新索引

  STEP 4-B 之后，第 4 步必经过 `check_indexable()`。而生产现存 95 个 `knowledge/` 对象对应的历史文档**没有任何 `ContentModeration` 记录**，`is_document_indexable_by_moderation()` 对它们一律返回 `False`，于是 `index_document_task` 把每篇文档写成 `status='failed'`。

  执行结果：分块已删、向量库已删、文档状态全部 `failed`、RAG 返回 0 结果，**且没有任何恢复路径**（历史文档无法获得审核记录，见 P-2 / P-8）。
- **风险**：一条运维命令造成不可逆的全量知识库数据毁灭。
- **影响范围**：全部历史文档；`knowledge/` 全部 95 个对象；RAG 全链路；`/api/chat/`。
- **建议修复阶段**：**Phase 1-2（封版后的第一优先动作）** —— 在 STEP 4-C 启动前，先为该脚本加 `--dry-run` 与「必须先完成 `ContentModeration` 历史回填」的前置断言。

### P-2 CRITICAL ｜ ClamAV 不可达 ⇒ 新上传永久 `unavailable`，且 `confirmed_unchecked` 没有任何写入方

- **问题**：
  - 5.2 实测证明：检测链在 ClamAV 不可用时聚合为 `unavailable`，白名单不放行。
  - 但 `confirmed_unchecked` 在全仓库**零写入方**：
    - `apps/knowledge/admin.py` 仅 2 行（`from django.contrib import admin` + 注释），**`ContentModeration` 未注册**；
    - 无 API 端点、无 service 函数、无 management command（`apps/knowledge/management/commands/` 只有 `crawl_website.py`）、无脚本；
    - models.py:417-419 自述「本阶段只建立字段，不实现确认接口」；
    - `confirmed_by` / `confirmed_at` / `confirmation_reason` 三个字段存在但无人写入。
- **风险**：一次 ClamAV 故障或一段网络抖动 = **知识库永久停止增长**，运维无任何手段人工放行单篇文档。
- **影响范围**：全部新上传（5 个入口 + 分片合并）；新文档永远停在 `quarantine/`。
- **建议修复阶段**：**Phase 1-2** —— 人工放行接口（admin action 或管理命令 + 强制 `confirmed_by` / `confirmed_at` / `confirmation_reason` 三件套）必须在 STEP 4-C 之前或同批交付。

### P-3 HIGH ｜ RAG 读取路径完全没有审核门（防御纵深缺口）

- **问题**：检索侧三条路径全部只按 `status='indexed'` + 授权过滤，**从不读 `ContentModeration`**：
  - `_apply_hard_filters()`（rag.py:868）→ `authorized_knowledge_docs(user_context).filter(status='indexed')`
  - `_fallback_title_search()`（rag.py:992）→ `.filter(kw_query, status='indexed')`
  - `_filter_by_permission()`（rag.py:909）→ `.filter(id__in=doc_ids, status='indexed')`
  - `authorized_knowledge_docs()`（rag.py:820-853）→ 只看 `visibility` / `college_id` / `department_id` / `uploader_id`。

  对比：写入侧 `index_document_task` 已接入 `check_indexable()`。门只在**写入时**生效一次。
- **风险**：
  - 门不是持续性的。任何让 `status` 变成 `indexed` 的路径（管理员手工改、未来的错误回填、DB 直改）都会**直接进入 RAG**，审核门无法拦住。
  - 现存 95 个历史文档无审核记录却仍在 RAG 中 ——「未通过审核的文档不得进入 RAG」这一保证对历史数据**从未成立过**。
- **影响范围**：`/api/chat/` 全部检索路径；`references_detail` / `images` 引用的文档集合。
- **建议修复阶段**：**Phase 1-2 / STEP 4-C**（读侧补门，与 P-8 的历史回填同批设计，避免出现「回填前 RAG 全空」）。

### P-4 HIGH ｜ Celery 缺失时，整条安全链在 HTTP 上传请求内同步执行，超出 nginx 超时预算

- **问题**：
  - `utils/celery_dispatcher.safe_delay` 在 Celery/broker 不可用时降级为**同步调用**（实测日志：`[fallback-sync] Celery 未安装，xxx 改为同步执行`）。
  - `upload_service.schedule_moderation()` 用 `transaction.on_commit(...)`；而 `ATOMIC_REQUESTS=False`，且调用点（views.py:869 等）在 `atomic` 块**之外** ⇒ `on_commit` **立即触发**。
  - 实测确认 views.py:869 之后紧接着就是 `return Response(...)` ⇒ HTTP 响应必须等整条 **审核 → 提升 → 索引** 链跑完才返回。
  - 超时预算对照：`CLAMAV_TIMEOUT=30` + `LLM_MODERATION_TIMEOUT=60` + `VISION_MODERATION_TIMEOUT=120`（图片）+ OSS copy + 文本抽取 + Embedding HTTP，而 `deploy/nginx.conf` 的 `/api/knowledge/upload/` 只有 `proxy_read_timeout 120s`。**仅视觉审核一项的超时值就等于整个 nginx 读超时。**
  - `CELERY_TASK_SOFT_TIME_LIMIT=240` / `CELERY_TASK_TIME_LIMIT=300` 在同步模式下**完全不生效**。
- **风险**：上传请求 504；客户端重试 ⇒ 重复文档、重复 Embedding API 计费；`runserver` 单线程下并发上传直接串行阻塞。
- **影响范围**：5 个上传入口 + 分片合并；后端可用性。
- **建议修复阶段**：**Phase 1-2（部署编排）** —— 确保 Celery worker/beat 真实运行（见 P-5），或为上传入口提供「仅入队、不等待」的硬保证。

### P-5 HIGH ｜ 部署形态不一致：`runserver` + 端口 3000 vs nginx 代理 8000；且无 worker/beat 启动

- **问题**：
  - 仓库唯一启动脚本 `backend/start.bat` 使用 **Django 开发服务器** `manage.py runserver 0.0.0.0:3000`，而 `deploy/nginx.conf` 的 upstream 是 `127.0.0.1:8000`。**端口不匹配**，两者若同时生效则全站 502。
  - `start.bat` 只启动 web 进程，**不启动 Celery worker，也不启动 Celery beat**。仓库中亦无 systemd / supervisor / Docker 配置。
  - 后果：`config/celery.py` 中全部 beat 条目（含 STEP 4-B 新增的 `retry-failed-moderations`）在生产**永远不会被执行**。
- **风险**：
  - `retry-failed-moderations` 不运行 ⇒ `unavailable` / `error` 文档**永不重试**，`next_retry_at` / `attempt_count` / 退避机制形同虚设。
  - `cleanup-expired-upload-sessions` 不运行 ⇒ 分片上传会话永不回收，OSS multipart 残留分片**持续计费**。
  - `crawl-neusoft-website-daily` / `crawl-neusoft-website-weekly-full` / `generate-faq-weekly` / `refresh-daily-context` 全部停摆。
  - `runserver` 为单线程开发服务器，不具备生产承载能力。
- **影响范围**：全部定时任务；对外可用性；OSS 成本。
- **建议修复阶段**：**Phase 1-2（部署编排）** —— 统一端口、引入 WSGI 服务器、显式启动 worker + beat。

### P-6 MEDIUM ｜ 生产 settings 中 `DEBUG=True` / `ALLOWED_HOSTS` 含 `'*'` / CORS 全开

- **问题**：`config/settings.py` 是生产 settings（指向阿里云 RDS），但实测 `DEBUG=True`；`ALLOWED_HOSTS` 含 `'*'`；`CORS_ALLOW_ALL_ORIGINS = DEBUG`（settings.py:131）因而为 `True`。
- **风险**：
  - `DEBUG=True` ⇒ 未捕获异常返回带源码片段与局部变量的调试页，泄露代码结构、配置、可能的凭据。
  - `ALLOWED_HOSTS=['*']` ⇒ Host 头注入（密码重置链接投毒等）。
  - CORS 全开 + 携带凭据 ⇒ 任意站点可跨域读取已登录用户的 API 响应。
- **影响范围**：对外 API 全域。
- **建议修复阶段**：**Phase 1-2（上线前必须关闭）** —— 属部署配置，非业务代码。

### P-7 MEDIUM ｜ 向量后端不可用时 `search()` 静默返回空列表，故障被掩盖成「知识库没有内容」

- **问题**：`utils/milvus.py` 在 `pymilvus` 缺失时回退 ChromaDB；ChromaDB 也缺失时 `connect()=False`、`ensure_collection()=False`，而 `search()` **返回 `[]` 而非抛异常**（实测输出见 5.3）。`retrieve()`（rag.py:1191-1195）只在 `q_emb` 为真时追加 `vector_hits`，空返回会被静默吞掉，随后走标题降级搜索并可能返回 `[]`。
- **风险**：RAG 表现为「知识库中没有相关内容」而非「向量库不可用」，运维与用户都无法区分这两种完全不同的故障。
- **影响范围**：`/api/chat/` 检索；故障可观测性。
- **建议修复阶段**：**Phase 2（可观测性）**，或 STEP 4-C 顺带加健康检查探针。

### P-8 MEDIUM ｜ 95 个历史 `knowledge/` 对象全部无审核记录，且本阶段无回填方案

- **问题**：OSS 实测 `knowledge/` 95 个对象、`quarantine/` 0 个。这些历史文档不会有 `ContentModeration` 记录，`is_document_indexable_by_moderation()` 对它们一律 `False`。
- **风险**：任何一次重新索引（如 P-1）都会把它们打成 `failed`；而它们此刻又因 P-3 仍留在 RAG 中 —— 系统处于「读得到、改不动」的不一致状态。
- **影响范围**：全部历史文档与 RAG 结果集。
- **建议修复阶段**：**Phase 2（历史数据批量迁移，本阶段被明令禁止）** —— 建议方案：为存量文档批量创建 `state='confirmed_unchecked'` 的记录并同时填齐 `confirmed_by` / `confirmed_at` / `confirmation_reason`，作为显式的人工确认事实记录。

### P-9 LOW ｜ `scripts/index_all_docs.py` / `index_quick.py` 在 STEP 4-B 后功能失效

- **问题**：两者只处理 `status='uploaded'` 的文档并调用 `index_document_task.run()`。因为新门的存在，它们现在**无法索引任何文档**（全部在 `check_indexable()` 处 fail-closed 并被写成 `failed`）。
- **与 P-1 的区别**：它们**不会**破坏已 `indexed` 的文档（不重置状态、不删 `DocChunk`），因此危害等级显著低于 `reindex_all.bat`。
- **已知取舍**：`code-changes/2026-09-10.md:503` 已记录「未改动，会在 `check_indexable()` 处 fail-closed」；此前的 `P1-2B_Knowledge_Consumer_Audit_2026-09-10.md`（I7）也已标记该脚本需改造。
- **影响范围**：运维手工索引能力。
- **建议修复阶段**：**Phase 1-2**（与 P-1 同批加 `--dry-run` 与前置检查）。

---

## 七、修复阶段汇总

| 优先级 | 编号 | 一句话 | 建议阶段 |
|---|---|---|---|
| CRITICAL | P-1 | `reindex_all.bat` 会不可逆地清空知识库 | Phase 1-2（最先） |
| CRITICAL | P-2 | `confirmed_unchecked` 无写入方，ClamAV 故障即永久停摆 | Phase 1-2 |
| HIGH | P-3 | RAG 读取侧无审核门 | Phase 1-2 / STEP 4-C |
| HIGH | P-4 | 同步降级把整条链压进上传请求，超 nginx 超时 | Phase 1-2（部署） |
| HIGH | P-5 | 端口不匹配 + 无 worker/beat 启动，定时任务全停 | Phase 1-2（部署） |
| MEDIUM | P-6 | `DEBUG=True` / `ALLOWED_HOSTS='*'` / CORS 全开 | Phase 1-2（上线前） |
| MEDIUM | P-7 | 向量库故障静默降级为「无结果」 | Phase 2 |
| MEDIUM | P-8 | 95 个历史文档无审核记录，无回填方案 | Phase 2 |
| LOW | P-9 | 索引运维脚本失效 | Phase 1-2 |

---

## 八、审计结论

1. **代码层面：STEP 4-B 的 fail-closed 语义实现正确。** 白名单唯一（`INDEXABLE_MODERATION_STATES`）、入口唯一（`check_indexable()`）、写入侧 5 个状态全部拦截、无发现的绕过路径，且检测链在依赖不可用时正确地 fail-closed 而非误放行。Git working tree 完好，STEP 3 / 4-A / 4-A.2 / 4-B 的未提交修改完整保留，**未发现任何意外改动**。

2. **运行环境层面：本机不是生产环境，绝大部分组件无法在此验证。** 唯一取得的生产侧硬证据来自 OSS —— 而该证据揭示：`quarantine/` 0 对象、`knowledge/` 95 个历史对象，说明**隔离区上传与提升流程在生产从未真实执行过一次**。

3. **最紧迫的不是 STEP 4-B 的代码，而是它周边被忽略的三处**：`reindex_all.bat`（P-1，可一键毁灭知识库）、`confirmed_unchecked` 无写入方（P-2，故障即永久停摆）、部署形态不一致（P-5，定时任务全停）。这三项都应当在 STEP 4-C 启动**之前**处理。

4. **本次审计为只读。所有问题均未修复，仅按要求记录 `问题 → 风险 → 影响范围 → 建议修复阶段`。**

---

## 九、未能审计的项（需在生产环境执行）

| 项 | 原因 |
|---|---|
| 生产 MySQL（RDS）实际数据 | `rm-bp18t1fwh5xb9cj7hro.mysql.rds.aliyuncs.com` 在本机 DNS 不可解析（VPC 内网地址 + 白名单） |
| `content_moderation` / `moderation_attempt` 表实际行数与状态分布 | 同上 |
| `KnowledgeDoc` 实际 `status` 分布 | 同上 |
| migration 0009 是否已应用 | 同上 |
| Celery worker / beat 真实存活状态 | 本机无 broker（`6379` 关闭）、无 celery 模块 |
| ClamAV / clamd 真实存活状态 | 本机 `127.0.0.1:3310` 连接被拒 |
| Milvus 集合真实状态与向量数 | 本机 `19530` 关闭、无 pymilvus |
| Django HTTP 服务真实监听 | 本机 `8000` 关闭 |
| nginx 实际生效配置 | 仓库内 `deploy/nginx.conf` 与 `start.bat` 端口不一致，无法判定哪一份真实生效 |

> 建议：在**生产主机**上重跑本报告第三节的健康检查矩阵，以确认 P-4 / P-5 / P-7 的真实影响面。
