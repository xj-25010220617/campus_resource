# Phase 1-2 Production Readiness Verification

> **阶段**：生产部署最终验收 / C-1 ~ C-5 条件项验证
> **日期**：2026-09-11
> **分支**：`phase-1-1-security-stopgap`
> **验证环境**：开发机（Windows 11 / Python 3.11.9 / `backend\venv`）
> **前置**：[P1-2_Phase1_2_PostFix_Audit_Report_2026-09-11.md](../P1-2_Phase1_2_PostFix_Audit_Report_2026-09-11.md)
> **本阶段性质**：**只读验收**。未修改任何仓库代码、未修改测试、未安装依赖、未执行破坏性命令。

---

## 0. 结论速览

| 项 | 结论 | 依据等级 |
|----|------|----------|
| C-1 生产 WSGI | **BLOCKED** | 仓库 + 本机实测 |
| C-2 生产端口 | **NOT VERIFIED** | 仅仓库侧统一，生产不可达 |
| C-3 Celery 运行时 | **STATIC PASS / RUNTIME NOT VERIFIED** | 本机无 celery |
| C-4 生产 settings | **NOT VERIFIED**（仓库守卫 PASS） | 生产 `.env` 不可达 |
| C-5 RDS | **NOT VERIFIED** | DNS 不可解析 |
| C-5 Redis | **NOT VERIFIED** | 本机 6379 拒绝连接 |
| C-5 ClamAV | **NOT VERIFIED** | 本机 3310 拒绝连接 |
| C-5 Milvus | **NOT VERIFIED** | 本机 19530 拒绝连接 |
| C-5 OSS | **PASS** | 实测 bucket 可达 |
| C-6 历史 95 份文档 | **REQUIRES NEXT PHASE** | 未处理；统计因 RDS 不可达未能复测 |
| C-7 quarantine GC | **REQUIRES NEXT PHASE** | 本阶段范围外 |
| C-8 向量后端用户侧提示 | **REQUIRES NEXT PHASE** | 本阶段范围外 |

### 最终判定

```
DO NOT ENTER STEP 4-C
```

判定规则要求 C-1 ~ C-5 **全部 PASS**。实际：C-1 BLOCKED，
C-2 / C-3(RUNTIME) / C-4 / C-5(RDS·Redis·ClamAV·Milvus) 均为 NOT VERIFIED。
**且** C-6 历史 95 份文档虽非本阶段失败项，也不能作为放行理由。

---

## 1. Git 状态

```
git branch --show-current   →  phase-1-1-security-stopgap        ✅ 与要求一致
git status --short          →  23 个 M + 20 个 ??（见下），无新增改动
git diff --check            →  clean（无空白错误）
git diff --cached --stat    →  （空）未 staged 任何文件
```

**未执行**：`git add` / `git commit` / `git push` / `git reset` / `git checkout` /
`git clean` / `git stash` / 任何破坏性操作。

**未修改用户工作区**：本次验收全程只读取、只运行测试，`git status --short` 条目数与
Phase 1-2 结束时完全一致（23 modified / 20 untracked）。本验收唯一的产物是本文档
（新增的未跟踪报告文件）。

工作区改动构成（沿用上一阶段，原样保留）：

- **Modified（23）**：`apps/knowledge/{admin,chunked_views,models,moderation,serializers,tasks,test_moderation_service,tests,views}.py`、
  `config/{celery,settings}.py`、`deploy/nginx.conf`、`scripts/{index_all_docs,index_quick}.py`、
  `start.bat`、`utils/{celery_dispatcher,data_query,milvus,oss,rag,shared_task_compat,sse}.py`、
  `code-changes/2026-09-10.md`
- **Untracked（20）**：`apps/knowledge/{confirmation,promotion,reindex_tool,upload_intent,upload_service,test_phase1_2_postfix,test_promotion_flow,test_upload_intent}.py`、
  `apps/knowledge/management/commands/{confirm_moderation,reindex_docs}.py`、
  `apps/knowledge/migrations/0009_*.py`、`apps/knowledge/templates/`、
  `deploy/{start_web,start_worker,start_beat}.bat`、`scripts/reindex_all.bat`、
  `database-changes/{chunked_upload_session,upload_intent}.md`、两份审计/报告文档

### 1.1 关键文件与报告一致性核对

| 文件 | 检查项 | 结论 |
|------|--------|------|
| `start.bat` | 已降级为"仅限本地开发"，端口 3000 | ✅ 一致 |
| `deploy/start_web.bat` | 无 runserver 回退；缺 WSGI 时 `exit /b 2` + BLOCKED | ✅ 一致 |
| `deploy/start_worker.bat` | `-A config`，任务清单含两个关键任务 | ✅ 一致 |
| `deploy/start_beat.bat` | 6 条调度全部列出 | ✅ 一致 |
| `deploy/nginx.conf` | 端口 8000、`server_name _` 占位、无硬编码域名 | ✅ 一致 |
| `config/settings.py` | P-6 四道守卫齐全 | ✅ 一致 |
| `config/celery.py` | `autodiscover_tasks()` + 6 条 beat 条目 | ✅ 一致 |
| `requirements.txt` | **无任何生产 WSGI 服务器** | ✅ 与 C-1 BLOCKED 一致 |

---

## 2. C-1：生产 WSGI 验证

### 2.1 依赖声明检查（仓库）

```
$ grep -inE "waitress|gunicorn|uwsgi|uvicorn" backend/requirements.txt
(无匹配)
```

`requirements.txt` 共 43 行，**不含任何生产 WSGI 服务器**。

### 2.2 运行环境检查（本机，只读 import 探测）

```
waitress     not installed
gunicorn     not installed
uwsgi        not installed
uvicorn      not installed
```

未执行任何 `pip install`，未修改 `requirements.txt`。

### 2.3 `deploy/start_web.bat` 行为核对（源码逐条）

| 要求 | 实现位置 | 结论 |
|------|----------|------|
| 不允许 `runserver` | 全文无 `runserver` 字样 | ✅ |
| WSGI 依赖不存在时明确退出 | [start_web.bat:55-67](../backend/deploy/start_web.bat#L55-L67) | ✅ |
| 返回 `exit code 2` | `exit /b 2`（第 66 行） | ✅ |
| 输出规定文案 | `[BLOCKED] production WSGI dependency/plan required`（第 57 行） | ✅ |

### 2.4 结论

> ## C-1 = **BLOCKED**
> ## `BLOCKED: production WSGI dependency/plan required`

**依据**：依赖未声明（仓库静态证据）+ 未安装（本机实测）→ 生产 Web 入口目前**无法启动**。
这是上一阶段刻意的 fail-loud 设计，不是回归。**本阶段未修复**（禁止新增依赖）。

**⚠️ 附加未验证风险（需部署时确认）**：
`start_web.bat:72` 使用 `python -m waitress --listen=... --threads=8 ...` 形式启动。
waitress 官方文档记载的命令行入口是 **`waitress-serve`**；`python -m waitress` 是否可
直接运行本机无法实测（未安装 waitress），官方文档站从本环境不可达，
故记为 **NOT VERIFIED**。建议部署时改用 `waitress-serve --listen=0.0.0.0:8000 --threads=8 config.wsgi:application`。

---

## 3. C-2：生产监听端口验证

### 3.1 仓库侧端口一致性

| 文件 | 端口 | 说明 |
|------|------|------|
| `deploy/nginx.conf:17` | `server 127.0.0.1:8000;` | upstream |
| `deploy/nginx.conf:22` | `listen 80;` | 外网入口（**无 TLS**，见 §5.7 发现 F-1） |
| `deploy/start_web.bat:29` | `set "DEFAULT_PORT=8000"` | WSGI 监听 |
| `start.bat:38` | `set "PORT=3000"` | **刻意保留**，已降级为本地开发专用 |

→ **仓库侧已统一为 8000**（nginx 为唯一外网入口，以它为准）。`start.bat` 的 3000
属本地开发，与生产无关，脚本内注释已说明。

### 3.2 `CAMPUS_PORT` 用途

`CAMPUS_PORT` 仅出现在 `deploy/start_web.bat:30-34, 46`：作为**监听端口覆盖变量**，
未设置时取 `DEFAULT_PORT=8000`。

```bat
set "DEFAULT_PORT=8000"
if "%CAMPUS_PORT%"=="" ( set "PORT=%DEFAULT_PORT%" ) else ( set "PORT=%CAMPUS_PORT%" )
```

用途：部署时若生产实际端口不是 8000，无需改脚本，只设环境变量即可；同时提醒
必须与 `nginx.conf` 的 upstream **同步**修改。

### 3.3 生产实际端口

**开发机无法访问生产服务器**，未做任何 `netstat` / `Get-NetTCPConnection` / `curl`
对外检查（无目标可查）。

> ## C-2 = **NOT VERIFIED**
> ## `PRODUCTION_PORT_NOT_VERIFIED`

**依据**：仓库侧统一为 8000 有源码证据；生产实际监听端口**没有任何证据**。
**未使用开发机端口冒充生产验证结果。**

---

## 4. C-3：Celery Worker 运行时验证

### 4.1 运行时检查（本机）

```
$ python -c "import celery"
ModuleNotFoundError: No module named 'celery'
```

本地 venv 未安装 celery（`requirements.txt:29` 声明为 `celery>=5.3.0`，属本机未装），
因此以下命令**均无法执行**：

```
celery --version
celery -A config.celery inspect registered
celery -A config.celery inspect ping
```

未安装 celery，未创建任务，未修改 `config/celery.py`，未修改任何 task decorator，
未启动任何 worker。

### 4.2 静态验证（仓库源码，全部通过）

| 检查项 | 证据 | 结论 |
|--------|------|------|
| Celery app 存在并加载 Django settings | `config/celery.py:19-22` | ✅ |
| 自动发现各 app 的 tasks | `app.autodiscover_tasks()` @ `config/celery.py:25` | ✅ |
| `moderate_and_promote_task` 注册 | `@shared_task(bind=True, ignore_result=True)` @ `apps/knowledge/tasks.py:293-294` | ✅ STATIC PASS |
| `retry_failed_moderations_task` 注册 | `@shared_task(bind=True, ignore_result=True)` @ `apps/knowledge/tasks.py:430-431` | ✅ STATIC PASS |
| beat 指向该任务 | `'task': 'apps.knowledge.tasks.retry_failed_moderations_task'` @ `config/celery.py:59` | ✅ |
| 其余 5 个任务装饰器齐全 | `tasks.py:36/59/91/110/499`（extract_freshness / crawl_neusoft / refresh_daily_context / index_document / cleanup_expired_upload_sessions） | ✅ |

> ## C-3 = **STATIC PASS / RUNTIME NOT VERIFIED**

**依据**：源码层面的注册与调度关系完整；"运行期任务是否真的出现在 Celery app 的
注册表里"需要在装有 celery 的生产环境执行 `inspect registered` 才能确认。
**未用"本地缺 celery"冒充"生产注册失败"**。

---

## 5. C-4：生产 settings 验证

> **本报告不包含、也未读取任何 SECRET_KEY / 数据库密码 / OSS AK·SK / JWT Secret /
> Redis 密码 / LLM API Key 的值。** 下文只报"是否设置"与"是否满足安全条件"。

### 5.1 仓库守卫（静态，全部 PASS）

| 要求 | 实现 | 结论 |
|------|------|------|
| `DEBUG` 缺省为 False | `DEBUG = _cfg_bool('DJANGO_DEBUG','DEBUG', default=False)` @ [settings.py:48](../backend/config/settings.py#L48) | ✅ |
| 生产禁用默认开发密钥 | `if not DEBUG and SECRET_KEY == _DEV_SECRET_KEY: raise` @ [settings.py:51-54](../backend/config/settings.py#L51-L54) | ✅ |
| `ALLOWED_HOSTS` 不能为空 | 空且非 DEBUG → `ImproperlyConfigured` @ [settings.py:59-66](../backend/config/settings.py#L59-L66) | ✅ |
| `ALLOWED_HOSTS` 不能含 `*` | `if not DEBUG and '*' in ALLOWED_HOSTS: raise` @ [settings.py:68-74](../backend/config/settings.py#L68-L74) | ✅ |
| `CORS_ALLOW_ALL_ORIGINS=False` | `if not DEBUG and CORS_ALLOW_ALL_ORIGINS: raise` @ [settings.py:188-193](../backend/config/settings.py#L188-L193) | ✅ |
| `CORS_ALLOWED_ORIGINS` 生产不兜底 localhost | `default='http://localhost:5173' if DEBUG else ''` @ [settings.py:196-199](../backend/config/settings.py#L196-L199) | ✅ |
| 旧变量名兼容 | `_cfg_bool('DJANGO_DEBUG','DEBUG')` / `_cfg_list('DJANGO_ALLOWED_HOSTS','ALLOWED_HOSTS')` / `_cfg_list('CORS_ALLOWED_ORIGINS','CORS_ORIGINS')` | ✅ |
| 新变量名优先 | 助手按参数顺序取第一个非空值，`DJANGO_*` 在前 @ [settings.py:25-42](../backend/config/settings.py#L25-L42) | ✅ |

以上每条均有对应测试（`ProductionSettingsGuardTests`，7 项）。**仓库侧守卫 PASS。**

### 5.2 生产 `.env` 可访问性

**生产 `.env` 不在仓库内，开发机无法访问生产服务器** → 无法确认生产环境
`DJANGO_DEBUG` / `DJANGO_ALLOWED_HOSTS` / `CORS_ALLOWED_ORIGINS` 的取值。

### 5.3 本机 `.env` 的取值（**仅代表开发机，不代表生产**）

只读取了非敏感键：

```
DEBUG                  = True          ← 旧变量名（兼容路径生效）
ALLOWED_HOSTS          = *             ← 旧变量名
CORS_ORIGINS           = http://localhost:5173
CELERY_BROKER_URL      = redis://127.0.0.1:6379/0
MILVUS_HOST / PORT     = localhost / 19530
DB_HOST                = rm-bp18t1fwh5xb9cj7hro.mysql.rds.aliyuncs.com
DB_NAME / DB_PORT      = campus_resource / 3306
OSS_BUCKET_NAME        = campus-resourse
OSS_ENDPOINT           = oss-cn-chengdu.aliyuncs.com
EMBED_BACKEND          = aliyun

SECRET_KEY             : SET (len=45)
DB_PASSWORD            : SET
OSS_ACCESS_KEY_ID      : SET
OSS_ACCESS_KEY_SECRET  : SET
QWEN_API_KEY           : SET
ALIYUN_EMBED_API_KEY   : SET
```

**解读**：本机 `.env` 是**开发档**（`DEBUG=True`），因此 §5.1 的四道守卫**按设计不触发**
（`DEBUG=True` 时 `*` 与 localhost 兜底均被允许）。这**不能**推断生产配置如何。

⚠️ 注意：本机 `.env` 使用**旧变量名**（`DEBUG` / `ALLOWED_HOSTS` / `CORS_ORIGINS`）。
兼容逻辑使它们仍生效——但生产若沿用旧名且写 `ALLOWED_HOSTS=*`，在 `DEBUG=False`
下会**拒绝启动**，这是预期行为。

### 5.4 `manage.py check --deploy`（本机执行，只读）

```
$ python manage.py check --deploy
System check identified some issues:   (87 条 warning，EXIT=0)
```

其中 **6 条为 Django 安全项**：

| 编号 | 内容 |
|------|------|
| `security.W004` | 未设置 `SECURE_HSTS_SECONDS` |
| `security.W008` | `SECURE_SSL_REDIRECT` 未设为 True |
| `security.W009` | SECRET_KEY 强度不足（< 50 字符 / 前缀 `django-insecure-`） |
| `security.W012` | `SESSION_COOKIE_SECURE` 未设为 True |
| `security.W016` | `CSRF_COOKIE_SECURE` 未设为 True |
| `security.W018` | **`DEBUG` 为 True** |

### 5.5 发现 F-1（新，非 P-1~P-9 回归）

```
$ grep -nE "SECURE_[A-Z_]*" backend/config/settings.py
(无匹配)
```

`settings.py` **完全没有** `SECURE_*` 系列设置——与生产/开发无关，任何部署都缺。
叠加 `deploy/nginx.conf:22` 的 `listen 80;`（**无 TLS**），意味着：

- 若生产经 HTTPS 终止于 nginx，Django 侧未声明 `SECURE_PROXY_SSL_HEADER`，
  `request.is_secure()` 恒为 False；
- 会话/CSRF Cookie 均非 secure-only，HSTS 与 SSL 跳转未启用。

`security.W009`（密钥强度）在本机因 `.env` 使用 45 字符密钥而触发；
生产密钥强度 **NOT VERIFIED**。

> **建议**（不在本阶段实施）：作为独立小批次处理 `SECURE_*` 与 TLS 终止，
> 并同时修正 nginx 的 `listen`。属新增安全加固项，与 C-1 的生产 WSGI 决策一并规划。

### 5.6 结论

> ## C-4 = **NOT VERIFIED**（仓库守卫 STATIC PASS）

**依据**：仓库侧四道守卫有源码 + 测试双证据；**生产 `.env` 实际取值无任何证据**。
未猜测、未推断。

---

## 6. C-5：生产依赖链路验证

全部为只读连通性检查。**未上传测试文件、未修改数据库、未修改 OSS、
未创建 collection、未删除任何对象、未 reindex**，
未执行 `reindex_all.bat` / `reindex_docs` / `index_all_docs.py` / `index_quick.py`。

### 6.1 实测结果

| 组件 | 目标 | 方法 | 结果 |
|------|------|------|------|
| RDS | `rm-bp18t1fwh5xb9cj7hro.mysql.rds.aliyuncs.com:3306` | DNS + TCP | **`getaddrinfo failed`（域名在开发机不可解析）** |
| RDS | Django ORM 只读 `SELECT 1` | `connection.cursor()` | **`OperationalError (2003) Can't connect ... [Errno 11001] getaddrinfo failed`** |
| Redis | `127.0.0.1:6379` | TCP + `PING`（RESP） | **`ConnectionRefusedError`（本机无 Redis）** |
| ClamAV | `127.0.0.1:3310` | TCP + `nPING` | **`ConnectionRefusedError`（本机无 clamd）** |
| Milvus | `localhost:19530` | TCP | **`ConnectionRefusedError`（本机无 Milvus）** |
| OSS | `oss-cn-chengdu.aliyuncs.com:443` | TCP | **OPEN** |
| OSS | bucket `campus-resourse` | `oss2.Bucket.get_bucket_info()`（只读） | **OK，storage_class=Standard** |

`manage.py check --deploy` 可正常加载生产 settings 并完成（EXIT=0），
说明**配置读取路径本身可用**；失败点在网络可达性。

### 6.2 逐项结论

```
RDS    = NOT VERIFIED   （开发机无法解析生产 RDS 域名）
Redis  = NOT VERIFIED   （本机 6379 拒绝连接；生产 broker 地址未验证）
Celery = NOT VERIFIED   （见 C-3；本地无 celery，无法 inspect）
ClamAV = NOT VERIFIED   （本机 3310 拒绝连接）
Milvus = NOT VERIFIED   （本机 19530 拒绝连接）
OSS    = PASS           （bucket 可达，凭据有效，只读检查）
```

### 6.3 重要限定

**以上"拒绝连接"全部发生在开发机本地回环地址上，只能证明"本机没跑这些服务"，
不能证明生产不可达。** 生产 RDS 的域名在开发机 DNS 上不可解析，说明它很可能是
VPC 内网端点——从公网/开发机不可达属预期，**不构成生产故障证据**。

---

## 7. 部署编排检查

### 7.1 拓扑（源码证据）

```
                    ┌─────────────────────────────┐
  Internet ──▶ nginx:80 ──proxy_pass──▶ WSGI 127.0.0.1:8000 ──▶ Django
                    └─────────────────────────────┘
   （deploy/nginx.conf:16-19 upstream / :22 listen / :27,39,50,61 proxy_pass）
                        （deploy/start_web.bat:29 DEFAULT_PORT=8000）

  Celery Worker ──▶ Redis      （deploy/start_worker.bat:49-53，-A config）
  Celery Beat   ──▶ Redis      （deploy/start_beat.bat:45-47，-A config）
```

依赖 `CELERY_BROKER_URL`（`settings.py:377`，默认 `redis://127.0.0.1:6379/0`）。
Worker 与 Beat 为**两个独立进程**，脚本注释明确禁止用 `-B` 合并。

### 7.2 Beat 调度条目（`config/celery.py:31-62`，全 6 条保留）

| # | 条目 | 调度 | 任务 |
|---|------|------|------|
| 1 | `generate-faq-weekly` | 每周一 03:00 | `apps.faq.tasks.generate_faq_weekly` |
| 2 | `crawl-neusoft-website-daily` | 每天 04:00 | `apps.knowledge.tasks.crawl_neusoft_website_task` |
| 3 | `crawl-neusoft-website-weekly-full` | 每周日 05:00 | 同上（max_pages=300） |
| 4 | `refresh-daily-context` | 每天 00:05 | `apps.knowledge.tasks.refresh_daily_context_task` |
| 5 | `cleanup-expired-upload-sessions` | 每天 03:30 | `apps.knowledge.tasks.cleanup_expired_upload_sessions_task` |
| 6 | **`retry-failed-moderations`** | **每 10 分钟** | **`apps.knowledge.tasks.retry_failed_moderations_task`** |

✅ **6 条全部保留，`retry-failed-moderations` 仍在**。本阶段未修改 `config/celery.py`
中的任何调度条目。

### 7.3 观察项 O-1（非阻塞）

`deploy/start_worker.bat:13-20` 的任务清单列出了 6 个任务，
但未列出 `apps.knowledge.tasks.refresh_daily_context_task`
（该任务存在于 `tasks.py:91-92` 且被 beat 条目 4 引用）。
因为 worker 用 `-A config` + `autodiscover_tasks()`，任务**实际会被注册**，
这只是注释清单不全，**不影响功能**。建议后续补全注释。

---

## 8. 上传链路异步安全性（静态验证）

### 8.1 创建点 → 派发点 一一对应

`KnowledgeDoc.objects.create()` 共 **5 处**，与 `schedule_moderation()` 的 **5 处调用**
精确一一对应，**没有任何创建路径跳过派发**：

| # | 创建点 | 所属视图 | 派发点 |
|---|--------|----------|--------|
| 1 | [views.py:836](../backend/apps/knowledge/views.py#L836) | `UploadCallbackView`（STS 直传回调） | [views.py:869](../backend/apps/knowledge/views.py#L869) |
| 2 | [views.py:1090](../backend/apps/knowledge/views.py#L1090) | `TextUploadView` | [views.py:1121](../backend/apps/knowledge/views.py#L1121) |
| 3 | [views.py:1529](../backend/apps/knowledge/views.py#L1529) | `FileUploadView` | [views.py:1577](../backend/apps/knowledge/views.py#L1577) |
| 4 | [views.py:1739](../backend/apps/knowledge/views.py#L1739) | `BatchFileUploadView` | [views.py:1787](../backend/apps/knowledge/views.py#L1787) |
| 5 | [chunked_views.py:383](../backend/apps/knowledge/chunked_views.py#L383) | `ChunkMergeView`（分片合并） | [chunked_views.py:416](../backend/apps/knowledge/chunked_views.py#L416) |

`UploadSTSView`（[views.py:644](../backend/apps/knowledge/views.py#L644)）只签发直传凭证
与 `UploadIntent`，**不创建 KnowledgeDoc**；实际创建发生在其回调 `UploadCallbackView`，
已由上表覆盖。

### 8.2 `schedule_moderation` → `dispatch_async`

`upload_service.schedule_moderation()` @ [upload_service.py:205](../backend/apps/knowledge/upload_service.py#L205)：

```python
transaction.on_commit(_dispatch)          # 提交后才入队
    └── dispatch_async(moderate_and_promote_task, doc_id)   # 只入队
        except DispatchUnavailable:
            logger.error(...)             # 只记 ERROR
            outcome.append(False)         # 返回 False
            return                        # 不重抛、不同步执行
```

✅ **上传后的 moderation orchestration 没有使用 `safe_delay()`**。
全仓库 `safe_delay` 在 knowledge 上传链路上的使用点仅下列几处，**均非上传后的审核编排**：

| 位置 | 用途 | 是否属 §九 范围 |
|------|------|-----------------|
| `promotion.py:125` | promotion 内部索引派发 | 否（由任务体内调用） |
| `tasks.py:362` | `moderate_and_promote_task` 内部索引派发 | 否 |
| `tasks.py:484` | `retry_failed_moderations_task` 重试派发 | 否（任务体内） |
| `tasks.py:489` | 补扫 `_recover_undispatched_uploads` | 否（beat 任务体内） |
| `config/celery.py:97` | 兼容任务转发 | 否 |
| `crawler.py:491` | 爬虫索引派发 | 否 |
| `views.py:1288` | `extract_freshness_task`（日期提取） | 否（非 moderation 编排） |
| `confirmation.py:202` | **人工确认放行**后的派发 | 否（管理操作，见 O-2） |

### 8.3 Celery 不可用时的行为

✅ 不执行 moderation / promotion / index；只记 ERROR、返回 `False`、
等待 recovery sweep。由 `DispatchAsyncTests` + `ScheduleModerationTests` 固定
（含 `test_queue_down_leaves_document_untouched` / `test_queue_down_does_not_raise`）。

### 8.4 `_recover_undispatched_uploads` 的边界

`tasks.py:372-427`，选取条件为**四个 must-all**：

```python
oss_key__startswith=QUARANTINE_KEY_PREFIX   # 仅隔离区，正式区历史文档不碰
status='uploaded'                           # 从未开始索引
created_at__lt=cutoff                       # 早于宽限期（默认 900s）
.exclude(id__in=ContentModeration.objects.values('doc_id'))   # 从未被审核
```

函数体只有 `dispatch(moderate_and_promote_task, doc_id)` 一句。
✅ **不做 approved / confirmed_unchecked / promotion / index 任何一项**，
不写入任何状态，只重新派发。

### 8.5 观察项 O-2（非阻塞，非 §九 违规）

`apps/knowledge/confirmation.py:202` 在人工确认放行后使用
`safe_delay(moderate_and_promote_task, doc_id)`。该路径由 **Django Admin Action /
manage.py 命令**触发（`transaction.on_commit` 内），**不是上传链路**，不属 §九 范围。
但需注意：Django Admin Action 运行在 HTTP 请求线程内，若该环境没有 Celery worker，
`safe_delay` 会**同步跑完整条 moderation → promotion → index**（注释
`promotion.py:278` 已说明此特性）。人工确认是低频、管理员显式触发、且有审计记录的操作，
风险可控，**本阶段只是记录，不作修改**。建议后续与 C-1 一并评估是否也改走
`dispatch_async`。

---

## 9. RAG fail-closed（静态验证）

### 9.1 唯一实现

```
utils/rag.py::_moderation_gated(qs)        # rag.py:856-878
        └── apps.knowledge.moderation.filter_indexable_docs(qs)   # moderation.py:879-911
```

`_moderation_gated` 只是一层转发，**不复制第二份白名单**。

### 9.2 门禁语义（fail-closed）

`filter_indexable_docs` @ [moderation.py:903-911](../backend/apps/knowledge/moderation.py#L903-L911)：

```python
gated = ContentModeration.objects.filter(
    doc_id=OuterRef('pk'),
    state__in=sorted(INDEXABLE_MODERATION_STATES), # 白名单 approved / confirmed_unchecked
    storage_state=STORAGE_STATE_PROMOTED,          # 必须已在 knowledge/ 正式区
)
return qs.filter(Exists(gated))
```

| 情形 | 结果 | 结论 |
|------|------|------|
| 无 `ContentModeration` 记录 | `Exists` 为假 → 排除 | ✅ 排除 |
| `storage_state != promoted` | 排除 | ✅ 排除 |
| `state ∉ INDEXABLE_MODERATION_STATES` | 排除 | ✅ 排除 |

### 9.3 接入点（4 处，`utils/rag.py`）

| 行 | 所在函数 | 通道 |
|----|----------|------|
| [914](../backend/utils/rag.py#L914) | `_apply_hard_filters` | SQL 硬过滤 → 候选 ID |
| [956](../backend/utils/rag.py#L956) | `_filter_by_permission` | 合并后的最终权限校验 |
| [1042](../backend/utils/rag.py#L1042) | `_fallback_title_search` | 标题降级检索 |
| [1145](../backend/utils/rag.py#L1145) | `retrieve_images` | 图片检索 |

### 9.4 `status='indexed'` 是否为绕过条件

**否。** 主路径 `retrieve()` 的调用图：

```
_apply_hard_filters(...)              → gated        → candidate_ids
   ├── milvus_client.search(filter_ids=candidate_ids)
   ├── _keyword_search(..., doc_ids=candidate_ids)          # rag.py:1281-1284
   ├── _keyword_table_search(..., doc_ids=candidate_ids)    # rag.py:1289-1291
   └── _chunk_content_search(..., doc_ids=candidate_ids)    # rag.py:1292-1294
_filter_by_permission(enriched, ...)  → gated（再次收窄）    # rag.py:1316
```

三个通道助手在 `utils/rag.py` 中的**唯一**生产调用点均传入 `doc_ids=candidate_ids`
（来自已过门的 `_apply_hard_filters`）；`if not candidate_ids` 时走的是同样过门的
`_fallback_title_search`。末尾 `_filter_by_permission` 再收窄一次。
`retrieve_images` 亦直接过门。

✅ **不存在以 `status='indexed'` 作为审核门绕过条件的生效路径。**

### 9.5 观察项 O-3（纵深防御缺口，**非现行漏洞**）

`_keyword_search`（[rag.py:582](../backend/utils/rag.py#L582)）、
`_keyword_table_search`（[rag.py:631](../backend/utils/rag.py#L631)）、
`_chunk_content_search`（[rag.py:658](../backend/utils/rag.py#L658)）三个助手**自身不设门**：

```python
qs = authorized_knowledge_docs(user_context).filter(kw_query, status='indexed')   # 只有 status='indexed'
if doc_ids is not None:
    qs = qs.filter(id__in=[...])          # 只有传了 doc_ids 才间接受限
```

`doc_ids` 默认 `None`。**当前所有生产调用点都传了已过门的 id，因此安全性成立**；
但若将来新增调用方漏传 `doc_ids`，这三条通道会退化为"仅 `status='indexed'`"，
而 `status='indexed'` 恰恰是 P-3 明确否定的充分条件。

> **建议**（本阶段不实施，仅记录）：把 `_moderation_gated()` 下沉进这三个助手内部，
> 使其自身 fail-closed，而不是依赖调用方传参。

同类：`_enrich_with_doc_meta`（[rag.py:980](../backend/utils/rag.py#L980)）只按
`id__in=doc_ids, status='indexed'` 取元信息，作用对象已是合并后的命中集（来源已过门），
风险低于上述三条，一并记录。

---

## 10. 历史 95 份文档（P-8 / C-6）

### 10.1 统计复测

**RDS 在开发机不可达**（`getaddrinfo failed`），无法执行任何只读统计查询。

> **历史文档统计 = NOT VERIFIED**
> （KnowledgeDoc 总数 / ContentModeration 总数 / 无 moderation 文档数 /
> official `knowledge/` 数 / `quarantine/` 数 —— **均未能复测**）

上阶段报告中的"95 份无 `ContentModeration` 记录的历史文档"来自生产侧审计，
本阶段**无法独立复核该数字**，故不重复断言。

### 10.2 本阶段是否回填 / 删除 / reindex（静态证据）

| 动作 | 证据 | 结论 |
|------|------|------|
| 自动回填 `confirmed_unchecked` | 全仓库写入方 **只有** `apps/knowledge/confirmation.py`（人工确认 service）；`reindex_tool.py` 仅**计数**（`stats['confirmed_unchecked']`） | ✅ 未回填 |
| 批量创建 `ContentModeration` | 无 `bulk_create`；无 `ContentModeration.objects.filter(...).update(...)`；仅有 `moderation.py:740` 的 `filter(pk=...).update(...)` 单条 CAS | ✅ 未批量写 |
| 删除历史数据 | 本阶段未执行任何写操作 | ✅ 未删除 |
| reindex | 未执行 `reindex_all.bat` / `reindex_docs` / `index_all_docs.py` / `index_quick.py` | ✅ 未 reindex |
| 批量重置 `status` | 未执行任何 `update()` | ✅ 未重置 |

> ## C-6 = **REQUIRES NEXT PHASE**
> （本阶段未处理；**不是本阶段失败项**，但也不能作为进入 STEP 4-C 的理由）

---

## 11. 测试结果

命令：`python manage.py test --settings=config.settings_test`

### 11.1 `apps.knowledge`

```
Ran 513 tests in 101.623s
OK (skipped=1)
```

✅ 与 Phase 1-2 结束时的 513 OK **完全一致**。

### 11.2 全量

```
Ran 545 tests in 97.198s
FAILED (failures=15, errors=4, skipped=1)
```

---

## 12. Baseline 对比（逐条）

| 版本 | 测试数 | failures | errors | skipped | 失败分布 |
|------|--------|----------|--------|---------|----------|
| Phase 1-1 baseline | 471 | 15 | 4 | 0 | 全部 `apps.accounts` |
| Phase 1-2 post-fix | 545 | 15 | 4 | 1 | 全部 `apps.accounts` |
| **本次验收** | **545** | **15** | **4** | **1** | **全部 `apps.accounts`** |

测试数增量 `545 − 471 = 74`，即新增的 `test_phase1_2_postfix.py`。

### 12.1 失败集合逐条比对（19 条，**与 baseline 完全一致**）

| # | 结果 | 测试 | 归属 |
|---|------|------|------|
| 1 | ERROR | `test_callback_jwt_can_access_protected_endpoint` | `apps.accounts.tests.SsoMockTests` |
| 2 | ERROR | `test_callback_returns_existing_user` | `apps.accounts.tests.SsoMockTests` |
| 3 | ERROR | `test_callback_updates_existing_user_info` | `apps.accounts.tests.SsoMockTests` |
| 4 | ERROR | `test_sso_user_can_access_me` | `apps.accounts.tests.EdgeCaseTests` |
| 5 | FAIL | `test_callback_all_three_codes_work` | `apps.accounts.tests.SsoMockTests` |
| 6 | FAIL | `test_callback_creates_new_user` | `apps.accounts.tests.SsoMockTests` |
| 7 | FAIL | `test_callback_empty_string_code` | `apps.accounts.tests.SsoMockTests` |
| 8 | FAIL | `test_callback_invalid_code` | `apps.accounts.tests.SsoMockTests` |
| 9 | FAIL | `test_callback_missing_code` | `apps.accounts.tests.SsoMockTests` |
| 10 | FAIL | `test_callback_sso_uid_unique` | `apps.accounts.tests.SsoMockTests` |
| 11 | FAIL | `test_change_password_success` | `apps.accounts.tests.LoginApiTests` |
| 12 | FAIL | `test_change_password_weak_new` | `apps.accounts.tests.LoginApiTests` |
| 13 | FAIL | `test_change_password_wrong_old` | `apps.accounts.tests.LoginApiTests` |
| 14 | FAIL | `test_me_get` | `apps.accounts.tests.LoginApiTests` |
| 15 | FAIL | `test_me_put` | `apps.accounts.tests.LoginApiTests` |
| 16 | FAIL | `test_me_put_cannot_change_role` | `apps.accounts.tests.LoginApiTests` |
| 17 | FAIL | `test_sso_login_returns_mock_codes` | `apps.accounts.tests.SsoMockTests` |
| 18 | FAIL | `test_sso_login_returns_usage_hint` | `apps.accounts.tests.SsoMockTests` |
| 19 | FAIL | `test_sso_user_cannot_jwt_login` | `apps.accounts.tests.EdgeCaseTests` |

模块分布：`SsoMockTests` 11 + `LoginApiTests` 6 + `EdgeCaseTests` 2 = 19。
`apps.knowledge` 失败数 = **0**。

✅ **无新增 failure。** 未修改 `apps.accounts`、未跳过、未 xfail、未删除 baseline 测试。

### 12.2 唯一的 1 条 skip

`test_phase1_2_postfix.py:998` 的
`@unittest.skipUnless(...)` → `ProductionSettingsGuardTests.test_celery_registers_moderation_tasks`。
原因：本机 venv 未安装 celery（`ModuleNotFoundError`）。同组功能已由 4 条始终执行的
静态断言覆盖（beat 6 条目齐全 / `autodiscover_tasks()` 存在 / beat 指向正确任务 /
三个关键任务有 `@shared_task` 装饰器）。属**依赖缺失 skip**，非安全逻辑 skip。
**本阶段未新增任何 skip / xfail。**

---

## 13. 剩余阻塞项

| 编号 | 阻塞项 | 状态 | 说明 / 建议 |
|------|--------|------|-------------|
| **B-1** | 生产 WSGI 服务器未声明、未安装 | **BLOCKED** | 在 `requirements.txt` 声明 `waitress>=3.0.0`（Windows）或改用 Linux + `gunicorn>=21.2.0`，然后 `pip install`。补齐后 `start_web.bat` 无需修改即可生效 |
| **B-2** | `start_web.bat` 的 `python -m waitress` 调用形式未验证 | NOT VERIFIED | 官方入口为 `waitress-serve`。建议改为 `waitress-serve --listen=0.0.0.0:%PORT% --threads=8 config.wsgi:application` |
| **B-3** | 生产实际监听端口未确认 | `PRODUCTION_PORT_NOT_VERIFIED` | 部署时确认；若 ≠ 8000，**同时**改 `nginx.conf` upstream 与设置 `CAMPUS_PORT` |
| **B-4** | Celery 运行时注册未确认 | NOT VERIFIED | 部署后执行 `celery -A config.celery inspect registered`，确认含 `moderate_and_promote_task`、`retry_failed_moderations_task` |
| **B-5** | 生产 `DJANGO_DEBUG` / `DJANGO_ALLOWED_HOSTS` / `CORS_*` 未确认 | NOT VERIFIED | 生产 `.env` 不在仓库；守卫已就位，配错会**拒绝启动**，可用"能否启动"作为间接验证 |
| **B-6** | RDS 可达性 | NOT VERIFIED | 生产内网端点，开发机不可达（预期）。需在生产机上验证 |
| **B-7** | Redis 可达性 | NOT VERIFIED | 本机 6379 无服务；生产 broker 地址未知 |
| **B-8** | ClamAV 可达性 | NOT VERIFIED | 本机 3310 无服务；**若生产亦不可用，新文档将全部停在 `unavailable`**，需人工确认放行 |
| **B-9** | Milvus 可达性 | NOT VERIFIED | 本机 19530 无服务。P-7 已保证不可用时不伪装成空结果 |
| **B-10** | `SECURE_*` 全缺 + nginx 无 TLS（发现 F-1） | 新增安全项 | `settings.py` 无任何 `SECURE_*`；`nginx.conf:22` 为 `listen 80`。建议独立批次处理 HSTS / SSL 跳转 / secure cookie / `SECURE_PROXY_SSL_HEADER` |
| **B-11** | 历史 95 份文档处置 | **REQUIRES NEXT PHASE** | C-6，独立阶段 |
| **B-12** | `quarantine/` GC | **REQUIRES NEXT PHASE** | C-7，属 STEP 4-C |
| **B-13** | 向量后端不可用的用户侧提示 | **REQUIRES NEXT PHASE** | C-8，本阶段已提供 `rag.vector_backend_available()` + ERROR 日志 |

**观察项（非阻塞）**：O-1 worker 脚本任务注释不全（§7.3）；
O-2 人工确认走 `safe_delay`（§8.5）；O-3 三个 RAG 通道助手不自带门（§9.5）。

---

## 14. 下一步建议

1. **先解决 B-1**：这是唯一"生产 Web 服务无法启动"的硬阻塞，且决策成本低
   （一行依赖 + 一次 pip install）。建议同时决定 Windows(waitress) 还是 Linux(gunicorn)，
   因为该决策会影响 B-3（端口）与 B-10（TLS 终止位置）。
2. **在能访问生产的机器上完成 B-3 ~ B-9**：全部是"换一台能连内网的机器再跑一遍"的检查，
   命令已在本报告中给出，无需改代码。
3. **B-10（F-1）单独排期**：它是本阶段新发现、独立于 C-1~C-5 的安全加固项，
   不应混入 STEP 4-C。
4. **O-3（§9.5）建议尽早修**：当前无生效漏洞，但"三个通道助手不自带门"是将来最容易
   被新调用方踩中的一类缺陷，修复成本低。
5. **C-6 / C-7 / C-8 保持冻结**：按纪律不在本阶段处理。

### 关于 STEP 4-C

```
DO NOT ENTER STEP 4-C
```

判定规则要求 **C-1 PASS ∧ C-2 PASS ∧ C-3 PASS ∧ C-4 PASS ∧ C-5 全部 PASS**，
且测试无新增 failure、`git diff --check` clean。

本次：测试无新增 failure ✅、`git diff --check` clean ✅，
但 **C-1 = BLOCKED，C-2 / C-3(RUNTIME) / C-4 / C-5 多为 NOT VERIFIED** ❌。

因此**不具备进入 STEP 4-C 的条件**。另需强调：C-6 历史 95 份文档暂不处理，
**不构成本阶段的失败**，但同样**不能**作为放行理由——两者是独立的判定项。

---

## 附录 A：本报告使用的只读命令

```bash
# Git
git branch --show-current; git status --short; git diff --check; git diff --cached --stat

# C-1
grep -inE "waitress|gunicorn|uwsgi|uvicorn" backend/requirements.txt
python -c "import importlib.util as u; u.find_spec('waitress')"

# C-3
python -c "import celery"

# C-4
python manage.py check --deploy
grep -nE "SECURE_[A-Z_]*" backend/config/settings.py
# （只读取 .env 的键名与下列非敏感键：DEBUG / ALLOWED_HOSTS / CORS_ORIGINS /
#   DB_HOST / DB_PORT / DB_NAME / CELERY_BROKER_URL / MILVUS_HOST / MILVUS_PORT /
#   OSS_ENDPOINT / OSS_BUCKET_NAME；密码类只判断"是否已设置"）

# C-5
python -c "socket.create_connection((host, port), timeout=4)"      # RDS / Milvus
python -c "socket.create_connection(...); s.sendall(b'PING\r\n')"  # Redis
python -c "socket.create_connection(...); s.sendall(b'nPING\n')"   # ClamAV
python -c "oss2.Bucket(...).get_bucket_info()"                     # OSS（只读）
python -c "django.setup(); connection.cursor().execute('SELECT 1')" # RDS 只读

# 测试
python manage.py test apps.knowledge --settings=config.settings_test
python manage.py test --settings=config.settings_test
```

**未执行**：`pip install` / `git add` / `git commit` / `git push` / `git reset` /
`git checkout` / `git clean` / `git stash` / 任何 migration / 任何 `reindex_*` /
任何 OSS 写操作 / 任何数据库写操作。

---

## 附录 B：本阶段产出

- **新增**：本报告 `docs/Phase1-2_Production_Readiness_Verification_2026-09-11.md`
- **修改**：无
- **删除**：无
- **安装依赖**：无
