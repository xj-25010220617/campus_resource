# Phase 1-0：前置环境核实报告

| 项目 | 内容 |
|---|---|
| 阶段 | Phase 1-0：前置环境核实（**只读，不实施任何修复**） |
| 项目代码目录 | `E:\github\campus_resource\backend` |
| 核实日期 | 2026-09-10 |
| 上一阶段 | `security_moderation_phase0_audit.md`（Phase 0 现状审计，结论 FAIL） |
| 核实原则 | 先确认事实，再修改代码；CODE FACT 与 PRODUCTION FACT 严格分离 |

---

## 0. 本次核实执行的只读操作清单（透明披露）

| 操作 | 性质 | 是否有写入副作用 |
|---|---|---|
| `git remote -v` / `branch` / `status` / `log` / `diff` / `show` / `rev-list` | 本地读取 | 无 |
| `git ls-remote origin refs/heads/main` | **网络读取**（不改本地 ref，未执行 fetch/pull） | 无 |
| `git cat-file -t <sha>` | 本地对象探测 | 无 |
| 读取 `.env`（仅读取，**未修改**；输出时脱敏） | 本地读取 | 无 |
| 读取 `requirements.txt` / `deploy/nginx.conf` / `.github/workflows/deploy.yml` / `docs/CONVENTIONS.md` | 本地读取 | 无 |
| 列目录 `venv/Lib/site-packages/*.dist-info`（等价 `pip list`，未执行 `pip install/freeze`） | 本地读取 | 无 |
| `curl -I` 匿名探测 OSS 对象与 ListObjects | **网络读取**（匿名 HEAD/GET 请求） | 无 |
| `bucket.get_bucket_acl()` / `get_bucket_info()` via oss2 | **OSS 只读 API 调用** | 无 |
| `grep` / `find` / `ls` | 本地读取 | 无 |

**未执行**：pull / checkout / reset / merge / fetch / commit / pip install / pip uninstall / pip freeze / migration / 任何 OSS 写操作（put/delete/copy/multipart）/ 任何数据库读写 / 修改任何文件 / 启停任何服务。

---

## 1. 核实结论

```text
CODEBASE_ORIGIN:            NO（同一仓库，但代码版本落后于生产，见 §2）
OSS_ACL:                    CONFIRMED  → PRIVATE
CELERY_WORKER:              UNKNOWN
REDIS:                      UNKNOWN
PRODUCTION_DEPENDENCIES:    UNKNOWN
NGINX_LIMIT:                CONFIRMED  → INCONSISTENT
DEPLOYMENT:                 UNKNOWN（进程托管方式可确认，启动命令无法确认）
```

### 1.1 ★ 最重要的核实结果（必须先读）

> **`CODEBASE_ORIGIN = NO`。当前工作副本 `b4c3774` 落后远程 main 至少 3 个提交，且这 3 个提交修改的正是 Phase 0 审计的核心文件（`views.py` / `serializers.py` / `chunked_views.py`）。**
>
> 其中一条已确认的修复直接影响 Phase 0 的风险结论：**分片上传 `file_size=0` 的绕过已在远程 main 被修复**（`ChunkUploadInitView` 现在拒绝 0 字节）。远程 main 还新增了「空文件 / 空文档拒绝」与「无文本内容时跳过 AI」。
>
> **因此：Phase 1 不得在当前副本上开工。** 必须先由人工把工作副本同步到远程 `main`（本阶段禁止执行 pull，故我未同步）。

### 1.2 另一个关键核实结果

> **`OSS_ACL = PRIVATE`（已确证）。** 这**下调**了 Phase 0 报告中的 R20（`oss_url` 字段存未签名公网 URL）：该字段虽然确实存的是不带签名的 URL，但由于桶 ACL 为 private，匿名访问返回 403，**该数据本身不可被利用**。R20 应从「待确认严重度」降级为 LOW/信息性。

---

## 2. 生产环境证据

### 2.1 代码来源核实

```text
代码来源核实：
Repository:    https://github.com/xj-25010220617/campus_resource.git   (origin)
Branch:        main
Commit:        b4c3774  (本地 HEAD, 2026-09-10 09:16:53 +0800, "真实后缀")
Working tree:  clean（已跟踪文件 0 处修改）
               仅 1 个未跟踪文件：security_moderation_phase0_audit.md（Phase 0 报告）
```

**远程 `main` 的实际版本**（`git ls-remote`，只读，未 fetch）：

```text
远程 main (refs/heads/main) = c402ed750deba5ae12a0c86f8d48f1ece7c49a13
本地最后一次 fetch 的 origin/main = 516d7ce9b5deec3bd763999db6284809ca511196
本地 main (HEAD)                 = b4c3774
```

**落后程度**：

```text
$ git rev-list --left-right --count origin/main...main
3       0        # origin/main 有 3 个提交不在本地 main 中
```

| 证据 | 结论 |
|---|---|
| `origin/main`（上次 fetch）比本地 HEAD 多 3 个提交 | 本地**至少**落后 3 个提交 |
| `git ls-remote` 得到的实际远程 main = `c402ed750` ≠ `516d7ce9`（本地 ref） | 远程还有**更多**本地不可见的提交 |
| `git cat-file -t c402ed750…` → `fatal: could not get object info` | **该提交对象本地不存在，无法审阅其内容** |

> **结论：本地副本落后远程 main ≥ 3 个提交，且远程最新状态在本机不可审阅。**
> **`CODEBASE_ORIGIN = NO`** —— 是同一个仓库（同 remote、同分支），但**不是同一个修订版本**。
>
> 补充：我也**无法证明生产服务器实际部署的版本**。CI 执行 `git pull origin main`，故生产**预期**为 `c402ed750`；但服务器是否成功拉取、是否存在本地未提交改动，均无法从本机确认 → 生产实际版本 = UNKNOWN。

**落后的 3 个提交（本地可见部分，`b4c3774..516d7ce9`）**：

| Commit | Message |
|---|---|
| `c68d3a0` | 解决文件上传的自动分类有问题，空文件上传，它也会自动乱读取，乱填信息问题 |
| `8c59bc4` | 修空文件上传问题 |
| `516d7ce` | 不允许空文件上传 |

修改人（据新增的变更记录）：**Fressia-zzt**

**改动文件（`git diff --stat main origin/main`）**：

```
 backend/apps/knowledge/chunked_views.py |   4 ++
 backend/apps/knowledge/serializers.py   |   5 +-
 backend/apps/knowledge/views.py         |  84 ++++++++++++++++++++++++--
 code-changes/2026-09-10.md              | 101 ++++++++++++++++++++++++
 4 files changed, 188 insertions(+), 6 deletions(-)
```

### 2.2 这 3 个提交对 Phase 0 审计结论的影响（重要）

| Phase 0 风险 | 远程 main 中的状态 | 判定 |
|---|---|---|
| **R6 后半段**：分片 `file_size=0` 时 merge 完整性检查被跳过 | `ChunkUploadInitView.post` 新增 `if file_size == 0: raise ValidationError(...)`（[chunked_views.py:114-116](backend/apps/knowledge/chunked_views.py) 远端版本）→ session.file_size 不再可能为 0 → merge 的 `if session.file_size and ...` 检查恒生效 | **已修复（远端）** |
| **R6 前半段**：分片链路无单文件/单分片大小上限 | diff 中**无**任何大小上限逻辑（只加了 `== 0` 的下限校验） | **仍存在** |
| 空文件 / 空文档可直接入库 | 5 个入口（file / batch / sts / callback / chunk-init）全部新增 0 字节拒绝；file / batch 新增"应含文本却提取为空则拒绝"；新增 `_pdf_has_content()` 区分空 PDF 与扫描件 PDF | **新增了输入校验**，但**这与内容安全审核无关** |
| **R2**（全链路无内容审核） | diff 中**无** `is_sensitive` / `sensitive_checked` / moderation / quarantine / 病毒扫描的任何改动 | **不受影响，结论仍成立** |
| **R1 / R4 / R5 / R7–R31**（callback oss_key 校验、隔离区、索引闸门、签名有效期等） | diff 中均无涉及 | **不受影响，结论仍成立** |
| **R28**（解析器依赖未声明） | **加重**：新增的 `_pdf_has_content()` 又引入了一个运行期 `import fitz`（PyMuPDF），该包**仍不在 requirements.txt 中** | **加重** |
| **R20**（未签名 `oss_url`） | 与代码无关；本次 OSS ACL 核实已确证为 private | **降级为 LOW（不可利用）** |

> **结论：Phase 0 审计的实质结论（无内容审核、无隔离、无审核闸门）在远程 main 上依然成立。** 但其中一条具体缺陷（`file_size=0` 绕过）已在远端修复，且新增了对 PyMuPDF 的运行期依赖。**Phase 1 开工前必须先把报告基线更新到 `c402ed75`。**

### 2.3 OSS 配置证据

来源：`backend/.env`（**只读，未修改**；AK/Secret 已脱敏，本报告不输出任何凭据）

```text
OSS_BUCKET_NAME = campus-resourse          ← 注意：桶名拼写为 resourse（非 resource）
OSS_ENDPOINT    = oss-cn-chengdu.aliyuncs.com   ← 成都区
OSS_ACCESS_KEY_ID     = <redacted>
OSS_ACCESS_KEY_SECRET = <redacted>
```

> 桶名拼写与既有团队记忆一致（`campus-resourse`）。**Phase 1 若新增隔离前缀，必须沿用该桶名，不要"顺手改正"拼写。**

### 2.4 依赖证据

- 声明来源：`backend/requirements.txt`（31 行，逐行核对）
- 本地环境来源：`backend/venv/Lib/site-packages/*.dist-info`（51 个包，**等价于 `pip list`，未执行任何 pip 写操作**）

### 2.5 部署证据

来源：`.github/workflows/deploy.yml`、`backend/start.bat`、`backend/deploy/nginx.conf`、`backend/docs/superpowers/specs/2026-09-01-redis-cache-rate-limit-design.md`

CI 部署脚本（`push` 到 `main` 触发，经 `appleboy/ssh-action` SSH 到生产机）：

```bash
cd /www/wwwroot/campus_resource
git pull origin main
cd backend
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python manage.py migrate
systemctl restart campus-backend
sleep 3
curl -sf http://127.0.0.1:3000/api/docs/ || { echo "后端启动失败"; exit 1; }
```

---

## 3. OSS 安全结论

### 3.1 逐项回答

| # | 问题 | 结论 | 证据来源 |
|:-:|---|---|---|
| A | **Bucket 是否公共读？** | **`PRIVATE`** | ① 带凭据调用 `bucket.get_bucket_acl()` → 返回值 `private`；② 匿名 `curl` 探测返回 403 |
| B | **`knowledge/` 对象是否可匿名访问？** | **否** | 匿名 `HEAD https://campus-resourse.oss-cn-chengdu.aliyuncs.com/<不存在key>` → **HTTP 403**（若为公共读，不存在的 key 应返回 404 NoSuchKey；403 表示匿名请求在鉴权层即被拒绝） |
| C | **是否必须经过签名 URL？** | **是** | 由 A/B 推出：匿名不可读，应用内所有下载/预览均走 `sign_url('GET', ...)`（[utils/oss.py:221](backend/utils/oss.py#L221)、[:244](backend/utils/oss.py#L244)） |
| D | **OSS endpoint 是否可从公网访问？** | **是（可解析、可建连、返回 403 而非超时）**。但要求已签名才能取对象。该 endpoint 本身是阿里云公网域名 | 匿名 `curl` 成功建连并收到 HTTP 403 响应 |
| E | **`quarantine` 前缀目前是否存在？** | **不存在** | 全仓库 grep `quarantine` / `pending/` / `unchecked` / `tmp-upload` → **仅命中一处无关结果**：`apps/feedback/urls.py:7` 的 `path('pending/', ...)`（反馈列表路由，与 OSS 前缀无关）。OSS 侧未做前缀枚举（属 OSS 读取操作，为稳妥起见仅以代码事实判定） |
| F | **是否存在 bucket policy / RAM policy / ACL？** | **Bucket ACL 已确证存在且为 `private`**。RAM policy / STS 角色策略**未核实（UNKNOWN）**——需在主账号 RAM 控制台查看，本机无该访问途径 | `get_bucket_acl()` 返回 `private`；`utils/oss.py:152-178` 的 AssumeRole 代码整段被注释 |

### 3.2 关键补充数据

```text
bucket         = campus-resourse
endpoint       = oss-cn-chengdu.aliyuncs.com
region         = cn-chengdu（成都）
storage_class  = Standard
bucket_acl     = private
匿名对象读     = 403 (拒绝)
匿名 ListObjects = 403 (拒绝)
```

### 3.3 正式 knowledge 路径现状

**CODE FACT**（[utils/oss.py:122](backend/utils/oss.py#L122) 及 4 处重复实现）：

```
knowledge/{school|college|department}/{owner_id}/{YYYY-MM}/{uuid12}.{ext}
爬虫专用：knowledge/website/0/{YYYY-MM}/crawl_{uuid12}.txt
```

- 该前缀**已确证为 private**，匿名不可读 → **未审核文件不会因"桶是公开的"而泄露**（这是本次核实的好消息）。
- 但 **未审核文件与已审核文件仍然混在同一前缀**，且客户端（STS 预签名 PUT）可直接写入该前缀 → Phase 0 的 R4（无隔离区）**依然成立**，只是"因桶公开而立即泄露"这一加成风险不存在。

### 3.4 OSS copy 能力（§5 必答）

**结论：`OSS copy support = NO`（当前封装不支持）。**

- `utils/oss.py` 的 `OSSClient` 共有 8 个公开方法：`get_sts_credentials` / `get_download_url` / `get_preview_url` / `compute_md5`（staticmethod）/ `check_duplicate` / `upload_text` / `delete` / `_sanitize_filename` / `_generate_oss_key`（后两个为私有）——**无 copy / move / promote**。
- 全仓库唯一的 `copy_object` 出现在运维脚本 [scripts/fix_oss_content_type.py:88](backend/scripts/fix_oss_content_type.py#L88)（用于原地改写 Content-Type），**未封装进 `OSSClient`**，且该脚本已因导入已删除的 `apps.resources.models.Resource` 而不可运行（Phase 0 R24）。
- 全仓库唯一的 `head_object` 同样仅出现在该脚本 [:65](backend/scripts/fix_oss_content_type.py#L65)。

**`utils/oss.py` 实际使用的 bucket 调用**（逐行核对，`self.bucket.` 的全部出现）：

| 行 | 调用 |
|---|---|
| 144 | `sign_url('PUT', oss_key, 300)`（dev 分支） |
| 168 | `sign_url('PUT', oss_key, 300)`（prod 分支，AssumeRole 被注释） |
| 221 | `sign_url('GET', oss_key, expires, params=params)` |
| 244 | `sign_url('GET', oss_key, expires)` |
| 313 | `put_object(...)` |
| 331 | `delete_object(oss_key)` |

> 即 `oss2.Bucket` 的 multipart 能力**只在 `apps/knowledge/chunked_views.py` 中被直接调用**（`init_multipart_upload` / `upload_part` / `complete_multipart_upload`），**不经过 `OSSClient` 封装**。

---

## 4. Celery 结论

| 项 | 结论 | 类别 |
|---|---|---|
| **Celery worker 是否运行** | **UNKNOWN** | PRODUCTION FACT |
| **Celery beat 是否运行** | **UNKNOWN** | PRODUCTION FACT |
| **Redis 是否运行** | **UNKNOWN** | PRODUCTION FACT |
| **Redis broker 地址（配置值）** | `redis://127.0.0.1:6379/0` | CODE FACT |
| **Redis broker 地址（生产实际）** | **UNKNOWN** | PRODUCTION FACT |

### 4.1 CODE FACT（可确认的代码事实）

| 事实 | 位置 |
|---|---|
| `CELERY_BROKER_URL` = `CELERY_RESULT_BACKEND` = `redis://127.0.0.1:6379/0` | [settings.py:215-216](backend/config/settings.py#L215)（`config()` 带 default 回退） |
| `app = Celery('campus_knowledge')`；`config_from_object('django.conf:settings', namespace='CELERY')`；`autodiscover_tasks()` | [config/celery.py:16-24](backend/config/celery.py#L16) |
| `beat_schedule` 定义 **5 个**定时任务（静态写在代码里） | [config/celery.py:30-53](backend/config/celery.py#L30) |
| `CELERY_TASK_TIME_LIMIT=300` / `SOFT_TIME_LIMIT=240` / `TASK_TRACK_STARTED=True` | [settings.py:215-223](backend/config/settings.py#L215) |
| **Celery 未被注册为 `INSTALLED_APPS` 条目**；无 `django_celery_beat` / `django_celery_results` | [settings.py:25-47](backend/config/settings.py#L25) |
| `beat_schedule` 中 5 个任务：`generate-faq-weekly`(周一03:00)、`crawl-neusoft-website-daily`(每日04:00)、`crawl-neusoft-website-weekly-full`(周日05:00)、`refresh-daily-context`(每日00:05)、`cleanup-expired-upload-sessions`(每日03:30) | [config/celery.py:30-53](backend/config/celery.py#L30) |

### 4.2 ★ 仓库内不存在 worker / beat 的启动配置

**CODE FACT —— 这是本阶段一个值得注意的发现：**

全仓库搜索 systemd unit / supervisor / docker-compose / Procfile / gunicorn 配置：

| 搜索目标 | 结果 |
|---|---|
| `*.service` / `*.timer` 文件 | **未找到（仓库内无任何 systemd unit 文件）** |
| `Dockerfile` | **不存在** |
| `docker-compose*` | **不存在** |
| `supervisor` 配置 | **不存在** |
| `Procfile` | **不存在** |
| `gunicorn` / `uwsgi` 配置或依赖 | **不存在**（`gunicorn` 亦未在 requirements.txt 中声明） |

- CI 只 `systemctl restart campus-backend` —— **只重启了 Web 服务**，脚本中**没有任何 worker / beat 的启动或重启步骤**。
- 因此：**Celery worker 与 beat 是否在生产运行，无法从仓库任何文件中证明。** 若生产部署严格来自本仓库的 CI，则 **worker / beat 均未被 CI 启动**；但它们可能由服务器上人工配置的 unit 托管（该 unit 文件不在仓库中）。
- `docs/superpowers/specs/2026-09-01-redis-cache-rate-limit-design.md` 提到部署拓扑为「单 Nginx + 单台 Gunicorn（多 worker）」，但**无对应配置文件**，且 `gunicorn` 未在依赖中声明 → 该文档描述与实际仓库不一致。

### 4.3 ★ `safe_delay` 同步 fallback 是否可能在生产实际生效

**结论：生产环境 = UNKNOWN；本地开发环境 = 已确证生效（YES）。**

**本地证据（CONFIRMED）**：

```
$ ls -d backend/venv/Lib/site-packages/*.dist-info | grep -iE "celery|redis"
（无输出）
```

**本地 venv（51 个包）中不存在 `celery`，也不存在 `redis`。**

由此可确证本地开发环境的降级链：

```
utils/shared_task_compat.py
  └─ import celery 失败 → 自行实现 shared_task
     └─ fn.delay = fn.apply_async = _inline_runner  → 同步执行
utils/celery_dispatcher.py::safe_delay
  └─ task.delay() 抛异常 → logger.warning → task(*args) 同步执行
```

→ **本地环境下，上传请求线程内即完成索引**（用户拿到 201 时 `status` 已是 `indexed`）。

**生产证据**：

- 无法访问生产服务器（`/www/wwwroot/campus_resource` 在远程主机；SSH 凭据仅以 GitHub Secret 形式存在，本机无访问途径）。
- 因此**无法确认**生产 venv 是否安装了 celery / redis，也**无法确认** worker 是否在运行 → **UNKNOWN**。

**对 Phase 1 的直接影响（不因 UNKNOWN 而改变）**：

> 由于生产状态为 UNKNOWN，**审核闸门不能建立在"生产一定是异步"的假设上**。Phase 0 的 R5 结论因此更加必要：**审核校验必须做在 `index_document_task` 任务体内部**——该处是异步路径（Celery）、`safe_delay` 同步降级、`shared_task_compat` 无 celery 降级**三者唯一的共同入口**。这是无论生产状态如何都成立的唯一可靠插桩点。

**未做任何修改**：`safe_delay` / `shared_task_compat` / `config/celery.py` 均未被触碰。

---

## 5. 依赖结论

### 5.1 核实表

| Package | requirements 声明 | 本地 venv 存在 | 版本 | 生产实际 |
|---|:--:|:--:|---|---|
| **oss2** | ❌ **未声明** | ✅ | 2.19.1 | UNKNOWN |
| **httpx** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **celery** | ✅ `celery>=5.3.0` (L18) | ❌ **不存在** | — | UNKNOWN |
| **redis** | ✅ `redis>=5.0.0` (L19) | ❌ **不存在** | — | UNKNOWN |
| **PDF parser (PyMuPDF/fitz)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **PDF parser (pdfplumber)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **PDF parser (pypdf/PyPDF2)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **DOCX parser (python-docx)** | ❌ **未声明** | ✅ | 1.2.0 | UNKNOWN |
| **XLSX parser (openpyxl)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **XLS parser (xlrd)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **PPTX parser (python-pptx)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **DOC parser (docx2txt)** | ❌ **未声明** | ❌ 不存在 | — | UNKNOWN |
| **Pillow** | ✅ `Pillow>=10.0.0` (L21) | ✅ | 12.3.0 | UNKNOWN |
| 其他已声明且本地存在 | `Django==4.2`✅ / `djangorestframework==3.14.0`✅ / `simplejwt==5.3.1`✅ / `cors-headers==4.3.1`✅ / `django-filter==23.5`✅ / `mysqlclient==2.2.0`✅ / `drf-spectacular==0.27.1`✅ / `python-decouple==3.8`✅ / `lxml>=5.0.0`✅(6.1.1) / `numpy>=1.26.0`✅(2.4.6) | | | |
| 其他已声明但本地**不存在** | `faster-whisper`❌ / `modelscope`❌ / `librosa`❌ / `soundfile`❌ / `pydub`❌ / `beautifulsoup4`❌ / `chromadb`❌ / `imageio-ffmpeg`❌ | | | |
| 系统二进制依赖（未声明） | `antiword`、LibreOffice `soffice`、`ffmpeg`（用于 `.doc` 解析与音频转码） | 未检测 | — | UNKNOWN |
| 安全 / 审核类依赖 | **`clamav` / `pyclamd` / `virustotal` / `bleach` 等全部不存在于 requirements 与本地 venv** | ❌ | — | UNKNOWN |

本地 venv 共 **51** 个包，完整清单见 §5.3。

### 5.2 ★ 必须严格区分的两个事实（按 §九 要求）

```text
CODE FACT:
  requirements.txt 中【没有】声明 oss2、httpx、PyMuPDF/fitz、pdfplumber、pypdf、
  python-docx、openpyxl、xlrd、python-pptx、docx2txt，也没有任何 AV/审核类依赖。

CODE FACT:
  本地开发 venv（51 包）中【存在】oss2==2.19.1、python-docx==1.2.0、Pillow==12.3.0；
  【不存在】celery、redis、httpx 以及除 python-docx 外的全部解析器。

PRODUCTION FACT:
  UNKNOWN —— 本机无生产服务器访问权限，无法执行 `pip list`。
  不能由 requirements.txt 的缺失推断生产未安装；
  也不能由本地 venv 的状态推断生产状态。
```

**因此本报告的表述是：**

> 依赖**声明**不完整（CODE FACT，已确证）；
> 生产环境实际安装情况**未知**（PRODUCTION FACT = UNKNOWN）。
> 即使生产恰好安装了这些包，**声明缺失本身仍是风险**——CI 每次部署执行
> `pip install -r requirements.txt`，声明缺失意味着**环境不可复现**：
> 换机、重建 venv、或 venv 损坏后重建，解析能力会静默消失。

### 5.3 本地 venv 完整包清单（51 个，只读采集）

```
Django-4.2  aliyun_python_sdk_core-2.16.0  aliyun_python_sdk_kms-2.16.5  asgiref-3.11.1
attrs-26.1.0  certifi-2026.6.17  cffi-2.0.0  charset_normalizer-3.4.7  crcmod-1.7
cryptography-49.0.0  django_cors_headers-4.3.1  django_filter-23.5
djangorestframework-3.14.0  djangorestframework_simplejwt-5.3.1  drf_spectacular-0.27.1
h11-0.16.0  h2-4.3.0  hpack-4.2.0  hypercorn-0.18.0  hyperframe-6.1.0  idna-3.18
inflection-0.5.1  jmespath-0.10.0  jsonschema-4.26.0  jsonschema_specifications-2025.9.1
lxml-6.1.1  mysqlclient-2.2.0  numpy-2.4.6  oss2-2.19.1  pillow-12.3.0  pip-26.1.2
priority-2.0.0  pycparser-3.0  pycryptodome-3.23.0  pyjwt-2.13.0  pymysql-1.2.0
python_decouple-3.8  python_docx-1.2.0  pytz-2026.2  pyyaml-6.0.3  referencing-0.37.0
requests-2.34.2  rpds_py-2026.6.3  setuptools-82.0.1  six-1.17.0  sqlparse-0.5.5
typing_extensions-4.15.0  tzdata-2026.2  uritemplate-4.2.0  urllib3-2.7.0  wsproto-1.3.2
```

> 注：`aliyun_python_sdk_core` / `aliyun_python_sdk_kms` 是 `oss2` 的传递依赖；`hypercorn` 表明本地曾以 ASGI 方式启动过；`pymysql` 与 `mysqlclient` 并存。

### 5.4 依赖缺失的直接后果（CODE FACT 推演）

`apps/knowledge/extractor.py` 中全部解析器均为**惰性导入 + try/except 降级**：

| 缺失的包 | 影响的提取器 | 后果 |
|---|---|---|
| `fitz`（PyMuPDF） | `_parse_pdf` 主路径 | 降级到 pdfplumber |
| `pdfplumber` | `_parse_pdf` 回退路径 | **PDF 提取失败 → 空文本** |
| `python-docx` | `_parse_docx` | DOCX 提取失败 → 空文本 |
| `python-pptx` | `_parse_pptx` | PPTX 提取失败 → 空文本 |
| `openpyxl` / `xlrd` | `_parse_xlsx` / `_parse_xls` | Excel 提取失败 → 空文本 |

> **对 Phase 1 的直接影响（须与 Phase 0 的 R28 合并考虑）**：
> 审核能力建立在"能提取到内容"之上。**若解析器缺失，提取结果为空 → 审核器面对空字符串 → 极可能判定"无可审内容"并放行**，形成**静默失效**。远程 main 新增的空文件校验（拒绝提取为空的文档）**部分**缓解了这一点，但**仅覆盖 `file` / `batch` 两个入口**，`callback` / `chunk-merge` / 爬虫路径仍无此校验。
> → Phase 1 必须补齐：**在 `index_document_task` 任务体内，"提取为空"必须视为审核不可完成 → 拒绝入库**，而不是放行。

---

## 6. Nginx / 上传限制结论

### 6.1 采集值

来源：`backend/deploy/nginx.conf`（全 43 行）

| 指令 | 值 | 所在 location |
|---|---|---|
| `client_max_body_size` | **`500m`** | `/api/knowledge/upload/` |
| `proxy_request_buffering` | **未设置**（nginx 默认 `on` → 先缓存整个请求体再转发） | — |
| `proxy_read_timeout` | `120s`（upload）/ `300s`（`/api/chat/`）/ `60s`（其余） | — |
| `proxy_send_timeout` | `60s` | `/api/chat/` |
| `proxy_connect_timeout` | `60s`（chat）/ `30s`（`/api/`） | — |
| `proxy_buffering` | `off`（仅 `/api/chat/`，为 SSE） | — |
| `upstream` | `server 127.0.0.1:8000; keepalive 16` | — |

### 6.2 应用层限制

| 限制 | 值 | 位置 |
|---|---|---|
| `FILE_SIZE_LIMITS`（按扩展名） | 最大 **ppt/pptx 50MB**、doc/docx 30MB；其余逐类型递减 | [serializers.py:95-125](backend/apps/knowledge/serializers.py#L95) |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | **100MB** | [settings.py:113](backend/config/settings.py#L113) |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | **100MB** | [settings.py:114](backend/config/settings.py#L114) |
| `DATA_UPLOAD_MAX_NUMBER_FIELDS` | **未设置**（Django 默认 1000） | — |
| `MAX_UPLOAD_SIZE` | **未设置**（不存在该设置） | — |
| **分片上传（chunk）大小限制** | **不存在** —— `ChunkUploadInitView` 仅校验 `file_size == 0`（远端新增）与 md5 格式、扩展名；**不校验上限**；`ChunkUploadView` 对单分片**无任何大小校验** | [chunked_views.py:94-229](backend/apps/knowledge/chunked_views.py#L94) |

### 6.3 结论

```text
Nginx upload limit:       500m（/api/knowledge/upload/）
Application upload limit: 50MB（ppt/pptx 上限）；100MB（Django 内存阈值）
Chunk upload limit:       无上限（仅拒绝 0 字节）
是否存在明显不一致：      是 —— INCONSISTENT
```

**不一致点（CONFIRMED）：**

1. **nginx `500m` ≫ 应用层 `50MB`**：nginx 不会拦截 50MB–500MB 的请求，拦截完全依赖应用层。同时 `proxy_request_buffering` 为默认 `on`，意味着**nginx 会先把整个请求体落盘/缓存后再转发**，应用层拒绝之前磁盘已被占用。
2. **分片链路完全无上限**：即使 nginx 的 `500m` 是唯一的"硬"上限，分片上传可通过多次小请求累计突破它。
3. **端口不一致（另一处独立发现）**：

| 来源 | 端口 |
|---|---|
| `deploy/nginx.conf` upstream | **8000** |
| `start.bat`（本地启动） | **3000** |
| `.github/workflows/deploy.yml` 健康检查 | **3000** |

   `deploy/nginx.conf` 的 upstream（8000）与 CI 健康检查、本地启动脚本（均为 3000）矛盾。结合该文件中 `server_name localhost`、`/static/` 指向占位路径 `/path/to/staticfiles/`，可判断 **`deploy/nginx.conf` 是开发/示例配置，并非生产实际使用的配置** → **生产实际 nginx 配置 = UNKNOWN**。

**未修改任何配置。**

---

## 7. 实际部署方式结论

```text
Deployment:   systemd（托管 Web 服务 campus-backend.service）—— 来自 CI 证据
              ⚠️ 但仓库内【不存在】任何 systemd unit 文件，unit 内容无法审阅
Web:          UNKNOWN（CI 执行 systemctl restart campus-backend，但启动命令不在仓库中）
              设计文档称 "Nginx + Gunicorn 多 worker"，但仓库无 gunicorn 配置，
              且 gunicorn 未在 requirements.txt 中声明 → 文档与实际不一致
Worker:       UNKNOWN（CI 脚本中无任何 worker 启动/重启步骤；仓库内无 unit 文件）
Beat:         UNKNOWN（同上）
Redis:        UNKNOWN（配置为 redis://127.0.0.1:6379/0，实际运行状态不可确认）
OSS:          CONFIRMED —— 阿里云 OSS，bucket=campus-resourse，
              endpoint=oss-cn-chengdu.aliyuncs.com，region=cn-chengdu，
              storage_class=Standard，ACL=private
Database:     UNKNOWN（依据 mysqlclient + pymysql + .env DB_* 判断为 MySQL；
              实例地址/版本/主从情况未核实，凭据已脱敏）
Nginx:        UNKNOWN（仓库内的 deploy/nginx.conf 疑为占位配置，见 §6.3）
反向代理:      CONFIRMED 存在（deploy/nginx.conf，neusoft 反向代理 + SSE 配置）
```

**本地开发部署方式（CODE FACT）**：`backend/start.bat` → `venv\Scripts\python.exe manage.py runserver 0.0.0.0:3000`（Django 开发服务器，非生产可用）。

---

## 8. Phase 1 影响

### 8.1 P1-1 是否可以开始？

**否 —— 不可在当前工作副本上开始。**

理由：当前副本 `b4c3774` 落后远程 `main` 至少 3 个提交（实际远程为 `c402ed75`，本地不可审阅），而这 3 个提交修改的正是 Phase 1 要动的同三个文件（`views.py`、`serializers.py`、`chunked_views.py`）。在该基线上修改会产生**必然的合并冲突**，且 Phase 0 报告中至少一条风险（R6 后半段）在远端已被修复，基线不一致会导致重复劳动与误判。

**前置动作（需人工执行，本阶段禁止我执行）**：

1. 把工作副本同步到 `c402ed75`（`git pull` 或等价操作）；
2. 同步后**重新核对** Phase 0 报告的 R6 / R20 / R28 三条结论（已在 §2.2 给出现有差异）；
3. 确认同步后的基线再进入 P1-1。

### 8.2 是否需要先修 OSS？

**不需要修 OSS 配置**，但**需要在代码中新增能力**。

- **好消息**：`OSS_ACL = private` 已确证 → Phase 0 的 R20 不可利用（LOW），**不需要为桶权限做紧急处置**。
- **仍需在 Phase 1-2 做**：`quarantine → knowledge` 转正所需的 **copy/move 能力在 `OSSClient` 中不存在**（§3.4）。这是**纯代码新增**，不涉及改桶策略。
- **本阶段未新增任何 OSS 能力**，仅做只读探测。

### 8.3 是否需要先修 Celery？

**不需要"修"，但必须先确认；且审核闸门的设计不依赖于确认结果。**

- 生产 worker / beat / Redis 状态 = **UNKNOWN**（§4）。**这不能作为阻塞项**，因为方案本身不依赖它：
- **审核校验必须做在 `index_document_task` 任务体内**——这是异步、`safe_delay` 同步降级、`shared_task_compat` 无 celery 降级**三条路径的唯一共同入口**。无论生产是否有 worker，该校验都生效。
- 额外提醒：**本地开发环境已确证为同步执行**（venv 无 celery，§4.3）。因此 Phase 1 开发与自测时，上传请求线程内就会完成索引 → **测试时必须意识到"上传即索引"，不能依赖"异步延迟"来掩盖审核时序问题**。

### 8.4 是否需要先补依赖？

**需要，且优先级高于 Phase 0 的判断。**

理由（CODE FACT）：

1. 解析器（PyMuPDF / pdfplumber / python-pptx / openpyxl / xlrd）**均未声明**；
2. 远程 main 新增的 `_pdf_has_content()` **又引入了一个运行期 `import fitz`**，仍未声明；
3. 解析失败 → 空文本 → **审核器面对空内容极可能放行**（静默失效链）；
4. CI 每次部署执行 `pip install -r requirements.txt` → 环境不可复现。

**但"补依赖"必须与"审核失败策略"一起做**：即使补齐依赖，仍应实现「提取为空 → 拒绝入库」，而不是依赖"依赖总是齐全"。

### 8.5 是否需要先确认生产环境？

**需要，但有 3 项在本机无访问权限，必须由人工在生产机上核实：**

| 待确认项 | 为什么重要 | 谁能确认 |
|---|---|---|
| 生产 venv 实际包列表（`pip list`） | 决定解析器与 celery 是否真实可用 | 需生产机访问权限 |
| Celery worker / beat / Redis 运行状态 | 决定索引是异步还是同步，影响审核时序与超时设计 | 需生产机访问权限 |
| 生产实际 nginx 配置与监听端口 | 仓库内 `deploy/nginx.conf` 疑为占位（端口 8000 vs 3000 矛盾） | 需生产机访问权限 |
| 生产实际部署的 commit | 确认是否等于 `c402ed75` | 需生产机访问权限 |

**这 4 项全部标为 UNKNOWN，未根据代码推断。**

---

## 9. 不允许自行修改的项目（下一阶段必须修改，但本阶段一律不动）

以下为 Phase 1 后续阶段（**不是本阶段**）需要改动的位置清单。**本阶段 0 处已修改。**

| # | 文件 / 位置 | 预计改动性质 | 备注 |
|:-:|---|---|---|
| 1 | `apps/knowledge/views.py` :: `UploadCallbackView` | 新增上传会话绑定校验 + `head_object` 存在性校验 + 停止采信客户端 md5 | **必须等基线同步后**（该文件在落后的 3 个提交中被改过） |
| 2 | `apps/knowledge/serializers.py` :: `KnowledgeDocSerializer.Meta.read_only_fields` | 纳入 `status` / `is_sensitive` / `sensitive_checked` / `visibility` | 同上，该文件已被改过 |
| 3 | `apps/knowledge/chunked_views.py` | 应用 `FILE_SIZE_LIMITS`（单文件上限）+ 单分片上限 | 同上，该文件已被改过 |
| 4 | `apps/knowledge/views.py` :: `_admin_scope_filter` | 显式处理 `visibility`（R26） | 影响 4 个复用点 |
| 5 | `config/celery.py` | 删除孤儿 `index_document` 任务（路径穿越 R11） | 需先确认无外部调用方 |
| 6 | `apps/knowledge/models.py` | 新增审核状态字段 | 需配套 migration |
| 7 | `apps/knowledge/migrations/` | 新增 migration | **本阶段禁止创建** |
| 8 | `utils/oss.py` | 新增 `copy_object` / `promote_object` 封装（转正能力） | 当前不存在 |
| 9 | `utils/moderation.py`（新建） | 统一审核服务 | 需遵守 `docs/CONVENTIONS.md` 目录规范（跨应用工具放 `utils/`） |
| 10 | `apps/knowledge/tasks.py` :: `index_document_task` | **审核闸门插桩点**（唯一可靠位置） | 必须覆盖异步 + 两种同步降级 |
| 11 | `utils/rag.py`（6 处）、`utils/sse.py`、`apps/chat/views.py` | 检索与引用条件叠加审核状态 | — |
| 12 | `utils/data_query.py` :: `_published_docs_queryset` | 改为授权过滤（R13） | — |
| 13 | `utils/milvus.py` :: `MilvusClient.search` | 空 `filter_ids` 保护（R31） | — |
| 14 | `requirements.txt` | 补齐 `oss2` / `httpx` / 解析器；如接 AV 再增依赖 | — |
| 15 | `config/settings.py` | 审核配置 + DRF throttling + `SECURE_*` | — |
| 16 | `deploy/nginx.conf` | 收紧 `client_max_body_size`；修正端口不一致 | 需先确认生产实际配置 |
| 17 | `scripts/index_all_docs.py` | 批量索引脚本需同步加入审核过滤 | 否则会把隔离区文档一并索引 |
| 18 | `config/middleware.py` | `DEBUG=True` 分支屏蔽 traceback | — |
| 19 | `scripts/fix_oss_content_type.py` | 修复对已删除 `Resource` 模型的导入 | — |
| 20 | **`code-changes/2026-XX-XX.md`** | **新增变更记录** | **强制**：`docs/CONVENTIONS.md` §12 要求每次提交必须记录 |
| 21 | **`database-changes/knowledge_doc.md`** | **新增表变更记录** | **强制**：`docs/CONVENTIONS.md` §13 要求表结构变更必须记录 |

### 9.1 必须遵守的项目规范（本阶段新采集到的重要约束）

**CODE FACT** —— 项目存在成文规范 `docs/CONVENTIONS.md`，Phase 1 实施时必须遵守：

- **§12 代码变更记录规范**：每次提交代码后，必须在 `code-changes/` 下新增/更新 `YYYY-MM-DD.md`，同日合并到一个文件；**禁止中文文件名或空格**。模板含 `# 代码修改记录 — YYYY-MM-DD`、`> **需求**`、`> **修改人**` 等字段。
- **§13 数据库变更记录规范**：每次表结构变化（新建表、改字段、加索引）必须在 `database-changes/` 下按表名新增/更新 `<table_name>.md`，含变更类型/日期/人员/原因/影响范围/**回滚方案**/SQL。
- **§1 项目结构**：跨应用复用代码放 `utils/`；敏感配置写 `.env` 并经 `decouple.config()` 读取并在 `settings.py` 提供 `default=` 回退。
- 本地已存在 `code-changes/`（含 2026-06-30 起的记录）与 `database-changes/` 目录。

> 参考范例：远端 main 的 `code-changes/2026-09-10.md` 是当前最新的变更记录写法（纯新增校验、明确声明"数据库变更：无"、"Migration 文件：无"、"API 变更：无/行为变化表"），Phase 1 应按同样结构撰写。

---

## 10. 最终状态

```text
========================================
Phase 1-0 Environment Verification
========================================

Codebase same-source:
NO
  └─ 同一仓库/同一分支，但本地 b4c3774 落后远程 main ≥3 提交；
     实际远程 main = c402ed75（本机对象不存在，无法审阅）

OSS bucket ACL:
PRIVATE
  └─ 已确证：带凭据 get_bucket_acl() → private

Anonymous OSS object read:
NO
  └─ 已确证：匿名 HEAD 不存在对象 → HTTP 403；匿名 ListObjects → HTTP 403

Celery worker:
UNKNOWN
  └─ 无生产机访问权限；仓库内亦无任何 worker 的 unit/配置/CI 启动步骤

Redis:
UNKNOWN
  └─ 配置值 redis://127.0.0.1:6379/0（CODE FACT）；运行状态不可确认

safe_delay production fallback:
UNKNOWN
  └─ 生产不可确认；但【本地开发环境已确证生效】（venv 无 celery）
  └─ 设计结论：审核闸门必须置于 index_document_task 任务体内，
     该点是异步/两种同步降级三条路径的唯一共同入口（不依赖生产状态）

Production parser dependencies:
UNKNOWN
  └─ 未知无法确认生产 venv；已知 requirements.txt 声明不完整
     （oss2/httpx/全部解析器均未声明）
  └─ 本地 venv：解析器仅有 python-docx；无 PDF/XLSX/PPTX 解析器；无 celery/redis

Nginx/application upload limit:
INCONSISTENT
  └─ nginx 500m / 应用 50MB(ppt) / 分片无上限 / proxy_request_buffering 未设置(默认 on)
  └─ 另：nginx upstream 8000 vs CI 健康检查与 start.bat 均为 3000
  └─ deploy/nginx.conf 疑为占位配置 → 生产实际 nginx 配置 = UNKNOWN

OSS copy support:
NO
  └─ OSSClient 无 copy/move；全仓库 copy_object 仅见于不可运行的运维脚本

Project modifications:
0

Database modifications:
0

OSS modifications:
0
  └─ 仅执行只读探测（get_bucket_acl / 匿名 HEAD），未写入/删除/复制任何对象

Next step:
WAIT FOR MANUAL REVIEW
```

---

## 附录：本阶段核实方法说明

**执行顺序**：git 来源核实 → OSS 配置与 ACL → Celery/部署 → 依赖 → nginx/上传限制 → 部署方式 → 汇总。

**未执行的操作（遵守只读约束）**：

- ❌ 未修改任何 Python 源码 / Django models / settings / nginx 配置 / Celery 配置 / `.env`
- ❌ 未创建 migration，未执行 migration
- ❌ 未修改 `requirements.txt`
- ❌ 未执行 `pip install` / `pip uninstall` / `pip freeze >`
- ❌ 未上传 / 删除 / 复制任何 OSS 文件
- ❌ 未读写数据库
- ❌ 未重启或修改任何生产服务
- ❌ 未安装任何软件（含 ClamAV）
- ❌ 未修改 Redis
- ❌ 未执行 `pull` / `checkout` / `reset` / `merge` / `fetch`

**明确标记为 UNKNOWN 的项（不做推断）**：

| 项 | 原因 |
|---|---|
| 生产实际部署的 commit | 无生产机访问权限 |
| 生产 venv 实际包列表 | 无生产机访问权限 |
| Celery worker / beat / Redis 运行状态 | 无生产机访问权限 |
| 生产实际 nginx 配置与端口 | 无生产机访问权限；仓库内配置疑为占位 |
| RAM policy / STS 角色策略 | 需主账号 RAM 控制台，本机无访问途径 |
| OSS 桶内是否存在非本系统写入的对象 | 未做前缀枚举（避免对 OSS 做超出必要范围的读取） |
| `c402ed75` 具体改动内容 | 该对象在本机不存在，无法审阅 |
| 系统二进制依赖（antiword / soffice / ffmpeg）生产可用性 | 无生产机访问权限 |

**报告生成位置（项目外）**：`e:\github\campus_resource\security_moderation_phase1_0_environment.md`

**本阶段严格遵守只读约束：项目修改 0 个文件，数据库修改 0 次，OSS 修改 0 次。**

**下一阶段：等待人工确认本报告后，再进入 Phase 1-1。**

---

# 附录 C：基线变更更正（Phase 1-0a，2026-09-10）

> **触发原因**：人工完成代码同步后，HEAD 由 `b4c3774` 变为 `c402ed75`。
> 经只读复核，**`c402ed75` 是本次核实时（出具本报告时）所无法看到的提交，其内容推翻了本报告 §2.2 的一条结论**。此处保留原结论并显式更正，以保全审计痕迹。

## C.1 同步后的基线状态（已确证）

```text
HEAD                = c402ed750deba5ae12a0c86f8d48f1ece7c49a13
远程 main (ls-remote) = c402ed750deba5ae12a0c86f8d48f1ece7c49a13   ← 完全一致
git rev-list --left-right --count origin/main...main = 0  0
branch              = main
working tree        = clean（仅 2 个未跟踪文件：Phase 0 / Phase 1-0 报告）
```

**CODEBASE_ORIGIN：由 `NO` 更正为 `YES`。** 本地副本现已与远程 main 完全一致。

> 备注：`CODEBASE_ORIGIN` 指的是「代码来源是否与生产同源」。此处 `YES` 的含义是**工作副本 == 远程 main == CI 部署源**；生产服务器是否已成功拉取该提交仍为 UNKNOWN（无生产机访问权限）。

## C.2 ★ 更正：本报告 §2.2 关于 R6 的结论**已失效**

本报告 §2.2 曾基于 `516d7ce9` 判定：

> ~~「R6 后半段（分片 `file_size=0` 时 merge 完整性检查被跳过）→ **已修复（远端）**」~~

**该判定现予撤回。** 原因是 `516d7ce9` 之后的两个提交：

| Commit | Message | 效果 |
|---|---|---|
| `5a21927` | Create 前端对接接口文档-空文件拒绝-2026-09-10.md | 新增接口文档 |
| **`c402ed7`** | **恢复允许空文件上传（撤销空文件拒绝逻辑）** | **完整撤销空文件拒绝逻辑**；同时删除 `code-changes/2026-09-10.md` 与该接口文档 |

`c402ed7`（作者 Fressia-zzt，2026-09-10 10:37）孤立 diff（`git show c402ed7`）确认撤销了**全部** 4 处空文件校验：

| 位置 | 被撤销的内容 |
|---|---|
| `chunked_views.py` :: `ChunkUploadInitView.post` | `if file_size == 0: raise ValidationError('不能上传空文件')` |
| `serializers.py` :: `FileUploadSerializer.file` | `error_messages={'empty': '不能上传空文件（文件内容为空）'}` |
| `views.py` :: `UploadSTSView.post` | `if file_size == 0: raise ValidationError(...)` |
| `views.py` :: `UploadCallbackView.post` | `if file_size == 0: raise ValidationError(...)` |
| `views.py` :: `FileUploadView.post` | `uploaded_file.size == 0` 拒绝 + 空文档拒绝 + `_pdf_has_content()` + 无文本跳过 AI |
| `views.py` :: `BatchFileUploadView.post` | 同上（批量版） |
| `code-changes/2026-09-10.md` | 变更记录文件被删除 |
| `docs/前端对接接口文档-空文件拒绝-2026-09-10.md` | 前端对接文档被删除 |

**只读复核结果（在 `c402ed75` 上执行）**：

```text
$ grep -rn "不能上传空文件\|_pdf_has_content" --include="*.py" apps/ utils/
（无输出）      → 已完全撤销

$ grep -n "file_size" apps/knowledge/chunked_views.py
110:  file_size = int(request.data.get('file_size') or 0)     ← 异常时回落为 0
112:  file_size = 0
176:  file_size=file_size,
271:  if session.file_size and total_size != session.file_size:   ← file_size==0 时该检查被跳过
```

→ **结论更正为：R6 前半段（无大小上限）与后半段（`file_size=0` 跳过 merge 完整性检查）在基线 `c402ed75` 上【均存在】，R6 完整成立。**

同理，本报告 §2.2 中「空文件/空文档已被拒绝」一项**同样失效**：当前基线**允许 0 字节文件与空文档上传**。

## C.3 在 `c402ed75` 上重新核实的 Phase 0 结论

| Phase 0 风险 | 在 `c402ed75` 上的状态 | 复核方式 |
|---|---|---|
| **R2** 全链路无内容审核 | **仍成立** | `grep -riE "moderation\|quarantine\|clamav\|pyclamd"` → 无输出 |
| **R3** `status`/`is_sensitive`/`sensitive_checked` 可被客户端写入 | **仍成立** | [serializers.py:38-43](backend/apps/knowledge/serializers.py#L38) 的 `read_only_fields` 仍**不含** `status` / `is_sensitive` / `sensitive_checked` / `visibility`，而 `fields` 仍包含它们 |
| **R3 附带**：两个字段仍是死字段 | **仍成立** | 全仓库对 `KnowledgeDoc.is_sensitive` 无任何写入/查询；命中的 `is_sensitive` 全部是 `utils/sensitive_filter.py` 内 `SensitiveResult` 类的**同名属性**（提问侧，与文档无关）+ 迁移文件中的字段定义 |
| **R4** 无隔离区 | **仍成立** | 无 `quarantine` 前缀；`OSSClient` 仍无 copy/move |
| **R6** 分片绕过 | **仍成立（本次更正）** | 见 C.2 |
| **R20** 未签名 `oss_url` | **不可利用（LOW）** | 本次 OSS ACL 核实确证桶为 `private`，匿名 403 |
| **R28** 解析器依赖未声明 | **减轻一点点**：`_pdf_has_content` 的额外 `import fitz` 已随撤销一并移除；但 `oss2`/`httpx`/全部解析器仍未在 `requirements.txt` 声明 | `cat requirements.txt` |
| **§9.7** Milvus 残留向量受候选集约束 | **仍成立** | `retrieve()` 仍先取 `candidate_ids = _apply_hard_filters(...)`，仍保留 `if not candidate_ids: ... return []`，Milvus 检索仍传 `filter_ids=candidate_ids` |

## C.4 RAG 代码已大幅变更（Phase 0 行号失效）

同步引入了一个大型 RAG 检索增强提交 **`c36971b`**（`utils/rag.py` 单文件 +319 行），新增内容：

| 新增 | 位置 |
|---|---|
| `_extract_query_terms()` — 统一查询词提取（停用词/中英混合/jieba/同义词扩展） | [rag.py:445](backend/utils/rag.py#L445) |
| `_select_relevant_chunk()` — 相关分块优先、首块兜底 | [rag.py:497](backend/utils/rag.py#L497) |
| `_build_doc_results()` — 统一结果构造 | [rag.py:527](backend/utils/rag.py#L527) |
| `_keyword_table_search()` — `Keyword.phrase` 反查 doc_id | [rag.py:607](backend/utils/rag.py#L607) |
| `_chunk_content_search()` — `DocChunk.content` 正文检索 | [rag.py:640](backend/utils/rag.py#L640) |
| `retrieve()` 管道接入两个新通道（第 2b 步） | [rag.py:1093](backend/utils/rag.py#L1093) |

**关键复核结论（好消息）**：**两个新检索通道都保留了 `status='indexed'` 门与授权过滤**，未引入新的绕过：

```python
# _keyword_table_search  →  rag.py:631
        id__in=doc_id_qs, status='indexed',
# _chunk_content_search  →  rag.py:658
        status='indexed',
```

且 `retrieve()` 仍以 `candidate_ids` 硬过滤约束全部通道（含新增两个），**Phase 0 §9「status='indexed' 是唯一状态门」的结论在新基线上依然成立**——只是 `utils/rag.py` 的**行号已全部失效**（文件从约 1022 行增至约 1300+ 行），Phase 0 报告 §9.2 表格与附录 B 中 `rag.py` 的行号需按新基线重新定位。

同样的行号漂移也适用于：`views.py`（空文件逻辑增删导致 ±84 行波动）、`serializers.py`、`chunked_views.py`。

## C.5 新增可用资产：`apps/knowledge/tests.py`

**在 Phase 0 时不存在**，现随 `c36971b` 引入（10,451 字节）。

```text
class KnowledgeDocSearchTests(TestCase)
  test_search_matches_original_file_name
  test_rag_search_matches_original_file_name
class RagRetrievalTests(TestCase)
  test_keyword_table_matches_document
  test_keyword_table_respects_permissions
  test_unindexed_document_not_searchable        ← ★ 与 Phase 1 审核门控直接相关
  test_keyword_search_matches_file_name
  test_chunk_content_search_matches_second_chunk
  test_chunk_content_search_respects_permissions
  test_fallback_selects_relevant_chunk_not_first
  test_retrieve_integrates_new_channels
  test_retrieve_respects_permissions
```

**对 Phase 1 的价值**：项目**已有**「未索引文档不可检索」「权限过滤生效」的测试范式。Phase 1 新增审核门控时，可直接照此范式补 `test_unmoderated_document_not_searchable` 等用例，而不必从零搭建测试基础设施。

**测试配置**：`config/settings_test.py`（151 字节）——`from .settings import *` + 内存 SQLite 覆盖。

> **本阶段未运行测试。** 运行 Django 测试会创建测试数据库（写入副作用），超出 Phase 1-0 的只读边界。测试是否通过 **未核实**。

## C.6 对 Phase 1 准备度的影响

| 问题 | Phase 1-0 原结论 | 更正后 |
|---|---|---|
| P1-1 是否可以开始？ | 否（基线落后） | **基线已就绪（`CODEBASE_ORIGIN = YES`）**；可进入 P1-1，但**须按 `c402ed75` 重新定位全部行号** |
| R6 是否需要修 | 仅前半段（无大小上限） | **前后两段都需修**（`file_size=0` 绕过仍然存在） |
| 空文件是否已处理 | 已在远端处理 | **未处理，已被撤销**；Phase 1 若希望维持「无可审内容即拒绝」的语义，需**自行实现**（且要注意本团队已有过「加了又撤」的先例，实施前应先与 Fressia-zzt 确认撤销原因） |
| 解析器依赖 | 未声明 | 不变（仍仅 `python-docx` 本地可用；`fitz` 的额外依赖随撤销移除） |
| 是否需补前端对接文档 | 未提及 | **需要**：`docs/前端对接接口文档-空文件拒绝-2026-09-10.md` 已随撤销删除，当前 `docs/` 下**不存在任何上传接口对接文档**（仅 `voice-ask-integration.md` 与 `superpowers/`、`test-docx/`）。若 Phase 1 改变上传接口行为，须新建文档交接前端 |

## C.7 本更正阶段的操作边界

**只读**：仅执行 `git log/show/diff/rev-list/ls-remote`、`grep`、`sed`、`cat`。

**未执行**：未运行测试、未创建测试库、未修改任何文件、未做任何 OSS/DB 操作。

**项目修改 0 个文件；数据库修改 0 次；OSS 修改 0 次。**

