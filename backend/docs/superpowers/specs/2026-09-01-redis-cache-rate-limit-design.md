# Redis 缓存 + 限流优化设计文档

- 日期：2026-09-01
- 范围：校园虚拟资源管理后端（Django 4.2 / DRF / MySQL / Celery）
- 状态：待评审
- 背景：正式上线后面向学校各部门 + 各实验室使用，需在百人级日常并发下保障读路径性能、防止昂贵外部调用被滥用。

---

## 1. 目标与约束

### 1.1 目标
1. 读路径压到 Redis 命中：静态字典、资源/FAQ 列表、聚合统计等高频读接口减少 MySQL 压力。
2. 昂贵调用受控：Chat（LLM+RAG）、语音（ASR+LLM）、AI 分类等外部调用有频率上限，防止单用户刷爆成本。
3. 认证安全：登录/回调类接口防暴力破解。
4. 防雪崩/防抖：Nginx 在最前端兜底，应用层可编程限流为主。

### 1.2 已确认约束（用户决策）
| 决策项 | 选择 |
|---|---|
| 部署拓扑 | 单机多进程（Nginx + Gunicorn 多 worker） |
| 规模预期 | 百人级日常（几十~几百并发） |
| Chat 缓存 | 只做**精确命中**（相同问题复用结果），不做向量相似语义缓存 |
| 限流层级 | 应用层 DRF + Nginx 层双层 |
| Chat 缓存命中响应 | **照常走 SSE 流式**，逐帧吐缓存文本（前端零改动） |
| 交付方式 | 先出设计文档，评审后再实施 |

---

## 2. 现状分析

### 2.1 缓存现状
- `config/settings.py:159-166`：`CACHES.default` = `FileBasedCache`（本地磁盘，`LOCATION=BASE_DIR/cache`，TTL 120s）。
  - 文件缓存跨进程共享，但性能差、易产生磁盘碎片；多 worker 下不一致。
- 缓存使用点：
  - `utils/query_rewriter.py`：Query 改写结果，key=`qr:{question}`，TTL 600s（走 Django cache → 文件）。
  - `apps/accounts/views.py:28-54`：钉钉扫码状态 `dd_state_*`，TTL 120s，取后即删。
- `utils/rag.py:31-58`：Embedding 结果进程内 LRU（TTL 300s，max 500，threading.Lock）——**多 worker 不共享**，每个 worker 各自缓存，命中率按 worker 数除。
- 无任何批量读接口缓存（资源列表、FAQ 列表、分类字典、dashboard 聚合均实时查库）。

### 2.2 限流现状
- 全项目零限流：无 `DEFAULT_THROTTLE_CLASSES` / `DEFAULT_THROTTLE_RATES`，无任何 `Throttle` 使用。
- 仅有业务层近似限制（非频率限制）：ask/ 问题 2000 字符上限、voice-ask/ 10MB 文件上限、批删/批量重置上限 100。

### 2.3 风险路径（无保护 + 高成本）
| 路径 | 权限 | 成本 | 风险 |
|---|---|---|---|
| `POST /api/chat/ask/` | **AllowAny（匿名可调）** | 一次请求串行多次 LLM + 向量检索 + 可能的联网抓取 + SSE | 匿名可无限刷爆 LLM 成本 |
| `POST /api/chat/voice-ask/` | IsAuthenticated | ASR + LLM + 向量检索 + SSE | 登录用户无频率限制 |
| `GET /api/admin/logs/dashboard/` | **AllowAny** | 一次聚合 5 类日志全表 count + 用户数 + 上传大小 | 游客可高频触发全表扫描 |
| `login/`、`refresh/`、钉钉/SSO callback | AllowAny | 写审计日志 / 外部 API | 暴力破解入口 |
| `ai-classify/` | admin | LLM 同步调用 | 管理员操作无频率限制 |

### 2.4 基础设施
- Redis 已部署，仅被 Celery 使用（`redis://127.0.0.1:6379/0`，见 settings.py:213-214）。
- `requirements.txt` 已有 `redis>=5.0.0`（redis-py），未装 `django-redis`。
- 部署：单 Nginx（deploy/nginx.conf）→ 单台 Gunicorn（多 worker）。

---

## 3. 设计

### 3.1 Redis 接入（基础层）

- 依赖：`requirements.txt` 增加 `django-redis>=5.4.0`。
- `CACHES` 调整：
  - `default` → Redis：
    - `BACKEND: django_redis.cache.RedisCache`
    - `LOCATION: redis://127.0.0.1:6379/1`（DB 1，与 Celery 的 DB 0 隔离，避免互相逐出）
    - `KEY_PREFIX: campus`
    - `TIMEOUT`：维持 120s 默认
    - `OPTIONS.CLIENT_CLASS: django_redis.client.DefaultClient`
    - `OPTIONS.MAX_CONNECTIONS`：按 Gunicorn worker 数 × 并发估算
  - 新增 `local`（FileBasedCache 保留）作为回退备胎。
- 开关与降级：
  - `settings.REDIS_CACHE_ENABLED`（默认 `True`，`config('REDIS_CACHE_ENABLED', default=True, cast=bool)`）。
  - 新增 `config/cache_health.py`：启动时 ping Redis；失败时打 `logging.error` 告警，并将 `django.core.cache` 的 `cache` 句柄切到 `local` 后端（fail-soft，服务不因 Redis 挂掉而不可用）。
  - 切后端的方式：在 settings 中用条件分支直接决定 `CACHES['default']` 指向哪个后端，避免运行时替换句柄的复杂度（运行时切换仅作为运维兜底）。
- 内存预算：百人级并发，缓存总量预计 < 100MB；Redis `maxmemory` 设 256MB + `allkeys-lru` 即可。

**顺带收益**：`qr:{question}` 改写缓存与钉钉状态缓存自动跨 worker 共享；DRF Throttle 计数天然跨 worker（见 §3.4）。

### 3.2 缓存分层

| 层 | 目标 | Key 结构 | TTL | 失效方式 |
|---|---|---|---|---|
| L0 静态字典 | colleges / disciplines / categories / departments / FAQ 分类 | `dict:{model}` | 6h | 版本号信号 +1 |
| L1 列表 | resources/ 列表、FAQ 列表、docs/ 列表 | `lst:{path}:{scope}:{params哈希}` | 120s | 版本号信号 +1 |
| L2 昂贵计算 | dashboard 聚合、upload-logs/stats、query 改写、embedding、OSS 签名 URL | `calc:{name}:{key}` | 30~600s 按类型 | TTL 自然过期 |
| L3 Chat 精确命中 | 完全相同问题的完整回答 + 推荐问题 | `chat:{规范化问题}` | 6h | TTL + 手动清 |

#### L0 静态字典
- 数据量小（学院/学科/分类/部门各几十条），全量序列化缓存。
- `get_or_set` + `hash` 长度校验，避免缓存损坏返回空。
- TTL 6h；学院/分类/部门变更走版本号失效（§3.3）。

#### L1 列表查询
- 缓存 key 必须包含 **scope**（角色 + college_id / dept），不同角色权限范围内数据不同，**严禁跨 scope 共享**：
  - 资源列表：`lst:resources:{role}:{college_id or 0}:{params哈希}`
  - FAQ 列表：`lst:faq:items:{college_id or 0}:{params哈希}`
  - 文档列表：`lst:docs:{scope}:{params哈希}`
- params 哈希：query_params 中过滤相关字段的有序拼接 → SHA1 前 12 位。
- 分页数据整体缓存（每页一个 key）。
- TTL 120s，写信号失效（§3.3）。

#### L2 昂贵计算
- **dashboard 聚合**：`calc:dashboard:{period}`，TTL 30s（随日志写入短期变化，短 TTL 保证近实时）。
- **upload-logs/stats**：`calc:upload_stats:{scope}:{group_by}`，TTL 60s。
- **Query 改写**：沿用 `qr:{question}`，切 Redis 后自动共享；TTL 600s 维持。
- **Embedding**：从进程内 LRU 迁到 Redis（`calc:embed:{question}`，TTL 300s）。保留进程内 LRU 作第一层（命中 0 网络开销），未命中再查 Redis，仍无则调 API 并双写。进程 LRU 上限维持 500。
- **OSS 签名 URL**：签名 URL 有有效期（一般 15min~1h），缓存至签名过期前（TTL 取签名有效期 - 60s），`calc:oss_url:{doc_id}:{user_id}:{action}`，避免重复签名 + 减少 OSS 往返。

#### L3 Chat 精确命中
- **规范化键**：问题去除首尾空白、全角/半角统一（`question.strip()` + 全角转半角），以规范化后的字符串为 key。
- **缓存内容**：最终回答文本 + 推荐问题列表（`generate_suggested_questions` 的结果），一并缓存。
- **命中处理**：照常走 SSE 流式（用户已确认），把缓存回答文本逐帧吐给前端；前端行为与实时回答完全一致。
- **不缓存**：涉及实时性/权限的请求（联网搜索分支、语音输入、含敏感词命中需拦截的）；命中仅针对普通知识问答主链路。
- **成本收益**：LLM 主回答 + 推荐问题是 ask/ 链路里最贵的两次调用，精确命中可直接省掉；校园场景高频重复问题（学费、校历、场馆时间等）命中率高。
- 风险控制：TTL 6h 保证知识库更新后 6h 内最终答案不陈旧；版本号方案不纳入 L3（问题多、更新低频），仅靠 TTL + 手动清缓存指令。

### 3.3 缓存失效：版本号 + 信号

- 每个可缓存模型维护版本 key：`ver:resource`、`ver:faqitem`、`ver:category`、`ver:discipline`、`ver:college`、`ver:department`、`ver:faqcategory`、`ver:document`。
- 查询类缓存 key 拼接 `:v{ver}`，`incr(ver)` 后旧版本 key 自然过期，无需逐条删除。
- 通过 `post_save` / `post_delete` 信号自动 `incr`：
  - Resource 增删改 → `ver:resource` +1
  - FAQItem / FAQCategory → `ver:faqitem` / `ver:faqcategory`
  - Category / Discipline / College / Department → 对应版本 key
  - KnowledgeDocument → `ver:document`
- 批量操作（`bulk_create` / `update`）不触发信号，需在批量写接口显式 `bump_version('resource')`。
- `utils/cache_utils.py` 提供：
  - `version_key(model_name)` → 当前版本号
  - `bump_version(model_name)` → incr（key 不存在则 set 1）
  - `build_key(prefix, model_name, *parts)` → `{prefix}:{model}:{parts...}:v{ver}`
  - `get_or_set_json(key, ttl, callable)` → 序列化安全的缓存助手
- 权衡说明：与「信号逐条删除具体 key」相比，版本号方案实现简单、不漏清、天然支持批量变更，代价是旧 key 短暂残留（靠 TTL 回收）。对百人级规模，残留量可忽略。

### 3.4 应用层限流（DRF Throttle，Redis 计数）

DRF Throttle 默认使用 `cache` 后端计数 → 切 Redis 后自动跨 worker 共享（单机多进程一致）。

#### Scope 定义（`DEFAULT_THROTTLE_RATES`）

| Scope | 端点 | 限流 | 依据 |
|---|---|---|---|
| `anon` | 所有 AllowAny 匿名 | 60/min/IP | 兜底 |
| `chat` | `ask/`（登录用户） | 10/min/用户 | LLM 成本 |
| `chat_anon` | `ask/`（匿名） | 3/min/IP | 防匿名刷 |
| `voice` | `voice-ask/` | 5/min/用户 | ASR+LLM 成本 |
| `login` | `login/`、`refresh/`、钉钉 callback、SSO callback | 10/min/IP | 防爆破 |
| `dashboard` | `admin/logs/dashboard/` | 30/min/用户 | 聚合保护 |
| `upload_classify` | `ai-classify/` | 10/min/用户 | LLM 分类 |
| `dingtalk_status` | `dingtalk/status/` | 60/min/用户 | 前端每秒轮询，需放宽防误伤 |

#### 类与接线
- `REST_FRAMEWORK` 增加：
  - `DEFAULT_THROTTLE_CLASSES`：`rest_framework.throttling.AnonRateThrottle` + `rest_framework.throttling.ScopedRateThrottle`
  - `DEFAULT_THROTTLE_RATES`：上表
- 视图接线（`throttle_scope` 或 `throttle_classes`）：
  - `AskView`：匿名用 `AnonRateThrottle` + `ScopedRateThrottle(scope='chat_anon')`；登录用户用 `scope='chat'`。实现：自定义 `throttle_classes`，按 `request.user.is_authenticated` 分派 scope。
  - `VoiceAskView`：`scope='voice'`。
  - `LoggedTokenObtainPairView`、`TokenRefreshView`、`DingtalkCallbackView`、`SsoCallbackView`：`scope='login'`。
  - `LogsDashboardView`：`scope='dashboard'`。
  - `AIClassifyView`：`scope='upload_classify'`。
  - `DingtalkStatusView`：`scope='dingtalk_status'`。
- 全局兜底：`AnonRateThrottle` 进 `DEFAULT_THROTTLE_CLASSES`，所有匿名请求受限。

#### 登录失败锁定（自定义 `FailedLoginThrottle`）
- 位置：`utils/rate_limit.py`。
- 逻辑：同一 IP（`REMOTE_ADDR`）+ 目标用户名 在 15min 窗口内累计失败 10 次 → 锁定 15min。
  - 计数键：`throttle:fail:{username}:{ip}`，TTL 15min。
  - 失败判定：登录接口 `validate_credentials` 失败时 `incr`；成功时 `delete` 对应计数。
- 429 响应带 `Retry-After` 头 + 中文文案（前端展示友好提示）。
- 与 `ScopedRateThrottle` 叠加使用（频率 + 失败锁定双保险）。

#### 限流响应
- DRF 默认返回 429 + `Retry-After`。前端需统一处理 429（弹提示 / 计数倒计时），此为前端配套改动项。

### 3.5 Nginx 层限流（最前端兜底）

`deploy/nginx.conf` 增加：

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=20r/s;
limit_req_zone $binary_remote_addr zone=chat:10m rate=5r/s;

location /api/chat/ask/ {
    limit_req zone=chat burst=10 nodelay;
    # ……现有 SSE 配置不变（proxy_buffering off 等）
}
location /api/ {
    limit_req zone=api burst=40 nodelay;
    # ……现有配置不变
}
```

- `rate` 为 IP 级粗粒度保护，应用层限流做细粒度（按用户）。两层不冲突。
- SSE 长连接：限制的是「建连频率」，连接建立后流式数据不受影响；登录后正常用户 5r/s 足够。
- 上传 location（`/api/knowledge/upload/`）单独保留，不放 `limit_req` 或放宽松 burst，避免大文件上传被限流打断。
- 钉钉状态轮询 `dingtalk/status/` 是 GET 且低频建连，20r/s 不误伤。

### 3.6 配套改动

1. **权限收紧**：`LogsDashboardView` 由 `AllowAny` 改为管理员可见（`IsAnyAdmin`）。理由：全表聚合 + 数据敏感，不应匿名访问。若确需游客可见仪表盘，至少保留限流。
2. **健康检查**：新增 `GET /api/health/`（`AllowAny`），返回 `{db, redis, celery: ok/error}` 状态，供监控探活。
3. **前端配套**：
   - 统一处理 429 响应（`Retry-After` 倒计时提示）。
   - Chat 精确命中缓存无需前端改动（SSE 行为一致）。

---

## 4. 文件与配置改动清单

| 文件 | 改动 |
|---|---|
| `requirements.txt` | `+ django-redis>=5.4.0` |
| `config/settings.py` | CACHES 切 Redis + 保留 local；`REDIS_CACHE_ENABLED`；REST_FRAMEWORK 加 THROTTLE 配置；Scope 速率表；`cache` 后备选 `local` |
| `config/cache_health.py`（新增） | 启动 ping Redis，失败告警 + fail-soft 切 local |
| `utils/cache_utils.py`（新增） | version_key / bump_version / build_key / get_or_set_json |
| `utils/rate_limit.py`（新增） | FailedLoginThrottle + 429 文案 |
| `apps/chat/views.py` | AskView / VoiceAskView 限流接线；L3 Chat 精确命中缓存（SSE 流式吐缓存） |
| `apps/accounts/views.py` | 登录相关视图限流接线；FailedLoginThrottle 接入；DingtalkStatus 放宽 scope |
| `apps/admin_panel/views.py` | dashboard 权限收紧 + 限流 + L2 聚合缓存 |
| `apps/knowledge/views.py` | 文档列表 L1 缓存；ai-classify 限流；OSS 签名 URL 缓存 |
| `apps/classify/views.py` | L0 字典缓存 |
| `apps/faq/views.py` | FAQ 列表 L1 缓存 |
| `apps/resources/views.py` | 资源列表 L1 缓存；upload stats L2 缓存 |
| `deploy/nginx.conf` | limit_req_zone + limit_req |
| `config/signals.py`（新增）+ `config/apps.py` 或各 app `apps.py` 的 `ready()` | 注册 post_save/post_delete → bump_version；统一在 `config/signals.py` 定义全部信号处理器，由 `config` 的 AppConfig `ready()` 导入一次 |

---

## 5. 测试与验证

### 5.1 单测
- `cache_utils`：version 递增、key 拼接、get_or_set_json 序列化。
- `rate_limit`：FailedLoginThrottle 计数、锁定、解锁、429 头。
- 限流 scope：各 scope 达到阈值后返回 429，`Retry-After` 存在。
- 缓存：
  - L1 列表：不同 scope（角色/学院）key 隔离，A 角色数据不出现在 B 角色缓存。
  - 版本失效：改 Resource 后旧 key 数据不再返回。
  - L3 Chat：相同问题第二次请求命中缓存，SSE 事件序列与实时一致（用 mock LLM 验证只调用一次）。

### 5.2 集成/压测（上线前）
- 并发压测读路径：资源列表 / FAQ / dashboard 命中缓存前后的响应延迟对比（目标：命中时 < 50ms）。
- 验证 Nginx limit_req 生效：超速请求返回 503（`limit_req_status 503` 可配置为 429 更友好）。
- 验证 Redis 挂掉时服务 fail-soft：kill Redis，读接口回退文件缓存，写接口正常，日志告警。

### 5.3 回滚
- 配置项 `REDIS_CACHE_ENABLED=False` 一键回到文件缓存；限流与缓存代码用 feature flag 包住，可灰度开关。

---

## 6. 上线顺序

1. Redis cache 接入 + 健康检查（基础设施层，独立可验）。
2. cache_utils + 版本号失效 + L0/L1 缓存（读路径提速）。
3. L2 聚合缓存（dashboard / stats）。
4. DRF 限流 scope + FailedLoginThrottle（应用层保护）。
5. L3 Chat 精确命中缓存（成本优化，需 mock 验证）。
6. nginx.conf limit_req（最前端兜底）。
7. 前端 429 处理配套。
