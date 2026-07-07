# Dashboard 聚合接口

> **接口归属**：`Admin Panel` 模块 — `LogsDashboardView`
> **文档版本**：2026-07-06（含「当前在线用户数」块）
> **适用环境**：本地开发 / 测试 / 生产（域名替换 base_url 即可）

---

## 1. 基本信息

| 项 | 值 |
|----|----|
| 方法 | `GET` |
| 路径 | `/api/admin/logs/dashboard/` |
| 权限 | `IsAuthenticated` + `IsSuperAdmin`（仅 `is_superuser=True`） |
| 鉴权 Header | `Authorization: Bearer <access_token>` |
| 响应格式 | `application/json; charset=utf-8` |
| 时区 | `Asia/Shanghai`（`settings.TIME_ZONE`） |
| 失败隔离 | 6 个统计块互不影响，单块失败不影响其它块 |

---

## 2. 请求参数（Query）

| 参数 | 类型 | 必填 | 默认 | 取值范围 | 说明 |
|------|------|------|------|----------|------|
| `period` | string | 否 | `day` | `day` / `week` / `month` | 决定 5 个统计块（upload/query/sensitive/login/operation）的时间区间 |
| `Authorization` | header | **是** | — | JWT Access Token | 不带或过期 → 401 |

### period 时间区间语义（Asia/Shanghai）

| 取值 | 区间起点 | 区间终点 |
|------|----------|----------|
| `day` | 今天 00:00:00 | 当前时刻 |
| `week` | 本周一 00:00:00 | 当前时刻 |
| `month` | 本月 1 日 00:00:00 | 当前时刻 |

> **online 块不受 period 影响**——无论传哪个 period，都是查"近 5 分钟在线"。

---

## 3. 完整响应结构（HTTP 200）

```json
{
  "period": "day",
  "start_at": "2026-07-06T00:00:00+08:00",
  "end_at":   "2026-07-06T16:48:00+08:00",
  "blocks": {
    "upload":    { "...": "..." },
    "query":     { "...": "..." },
    "sensitive": { "...": "..." },
    "login":     { "...": "..." },
    "operation": { "...": "..." },
    "online":    { "online_users": 12, "window_minutes": 5, "as_of": "2026-07-06T16:48:00+08:00" }
  }
}
```

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `period` | string | 回传请求中的 period |
| `start_at` | string (ISO 8601) | 区间起点（带时区） |
| `end_at` | string (ISO 8601) | 区间终点 = 服务端 `timezone.now()` |
| `blocks` | object | 6 个统计块（详见下表） |

---

## 4. blocks.* 各块字段表

### 4.1 `blocks.upload`（按 period 区间）

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 区间内上传日志总条数 |
| `total_size` | integer | 区间内上传文件总大小（字节） |
| `by_status` | `[{key, label, count}]` | 按状态分组（成功/失败/...） |
| `by_file_type` | `[{key, label, count}]` | 按文件类型分组（pdf/docx/...） |
| `by_college` | `[{key, label, count}]` | 按学院分组（仅 super_admin 全量） |

### 4.2 `blocks.query`（按 period 区间）

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 区间内查询日志总条数 |
| `avg_hit_count` | number | 平均召回命中数 |
| `avg_response_ms` | integer | 平均响应耗时（毫秒） |
| `like_rate` | number | 点赞率（`likes/total`，0~1，无数据时为 0） |
| `by_feedback` | `[{key, label, count}]` | 按反馈分组（赞/踩/无反馈） |

### 4.3 `blocks.sensitive`（按 period 区间）

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 区间内敏感内容日志条数（无分组） |

### 4.4 `blocks.login`（按 period 区间）

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 区间内登录尝试总条数 |
| `success_count` | integer | 成功数 |
| `fail_count` | integer | 失败数 |
| `by_success` | `[{key, label, count}]` | 按成功/失败分组 |
| `by_type` | `[{key, label, count}]` | 按登录类型分组（jwt/sso_mock/...） |
| `by_hour` | `[{key, label, count}]` | 按 24h 小时分桶（仅出现有事件的桶） |

### 4.5 `blocks.operation`（按 period 区间）

| 字段 | 类型 | 说明 |
|------|------|------|
| `total` | integer | 区间内操作日志总条数 |
| `by_action` | `[{key, label, count}]` | 按动作分组（create/update/delete/...） |
| `by_target_type` | `[{key, label, count}]` | 按目标类型分组（user/resource/category/...） |
| `top_operators` | `[{key, label, count}]` | 操作者 Top 10 |

### 4.6 `blocks.online`（**实时**，不受 period 影响）

| 字段 | 类型 | 说明 |
|------|------|------|
| `online_users` | integer | 近 5 分钟内有 `last_login` 且 `is_active=True` 的用户数 |
| `window_minutes` | integer | 判定窗口（固定 5） |
| `as_of` | string (ISO 8601) | 服务端统计时刻（带时区） |

---

## 5. 失败响应

### 5.1 单块计算失败（HTTP 仍 200）

```json
{
  "period": "day",
  "start_at": "...",
  "end_at":   "...",
  "blocks": {
    "upload":    { "total": 8, "...": "..." },
    "query":     { "error": "division by zero" },
    "sensitive": { "total": 0 },
    "login":     { "total": 18, "...": "..." },
    "operation": { "total": 4,  "...": "..." },
    "online":    { "online_users": 12, "window_minutes": 5, "as_of": "..." }
  }
}
```

> 前端应对每个块都做 `'error' in block` 判断，降级显示 "—" 或 "数据异常"。

### 5.2 HTTP 错误码

| HTTP | 含义 | 触发条件 |
|------|------|----------|
| 200 | 成功 | — |
| 401 | 未认证 | Token 缺失 / 过期 / 签名错误 |
| 403 | 无权限 | 当前账号 `is_superuser=False` |
| 500 | 服务异常 | 仅在视图最外层 try/except 触发 |

---

## 6. 完整调用示例

### 6.1 cURL

```bash
# 今日（默认 period=day）
curl -X GET "http://127.0.0.1:8000/api/admin/logs/dashboard/" \
  -H "Authorization: Bearer eyJhbGciOi..."

# 本周
curl -X GET "http://127.0.0.1:8000/api/admin/logs/dashboard/?period=week" \
  -H "Authorization: Bearer eyJhbGciOi..."

# 本月
curl -X GET "http://127.0.0.1:8000/api/admin/logs/dashboard/?period=month" \
  -H "Authorization: Bearer eyJhbGciOi..."
```

### 6.2 JavaScript（fetch）

```javascript
async function fetchDashboard(period = 'day') {
  const token = localStorage.getItem('access');
  const res = await fetch(
    `/api/admin/logs/dashboard/?period=${period}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const data = await fetchDashboard('day');
console.log('在线用户:', data.blocks.online.online_users);
```

### 6.3 Vue 3（axios）

```javascript
import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Authorization': `Bearer ${token}` }
});

async function fetchDashboard(period = 'day') {
  const { data } = await api.get('/admin/logs/dashboard/', {
    params: { period }
  });
  return data;
}

const dashboard = await fetchDashboard('day');
```

### 6.4 Python（requests）

```python
import requests

token = "eyJhbGciOi..."
r = requests.get(
    "http://127.0.0.1:8000/api/admin/logs/dashboard/",
    params={"period": "day"},
    headers={"Authorization": f"Bearer {token}"},
    timeout=10,
)
r.raise_for_status()
data = r.json()
print("在线:", data["blocks"]["online"]["online_users"])
```

---

## 7. 完整成功响应示例

```json
{
  "period": "day",
  "start_at": "2026-07-06T00:00:00+08:00",
  "end_at": "2026-07-06T16:48:00+08:00",
  "blocks": {
    "upload": {
      "total": 8,
      "total_size": 4194304,
      "by_status": [
        { "key": "success", "label": "成功", "count": 8 }
      ],
      "by_file_type": [
        { "key": "pdf",  "label": "pdf",  "count": 6 },
        { "key": "docx", "label": "docx", "count": 2 }
      ],
      "by_college": [
        { "key": "1", "label": "计算机学院", "count": 5 },
        { "key": "2", "label": "软件学院",   "count": 3 }
      ]
    },
    "query": {
      "total": 12,
      "avg_hit_count": 4.5,
      "avg_response_ms": 1620,
      "like_rate": 0.667,
      "by_feedback": [
        { "key": "1",  "label": "赞",     "count": 8 },
        { "key": "0",  "label": "踩",     "count": 1 },
        { "key": "-1", "label": "无反馈", "count": 3 }
      ]
    },
    "sensitive": {
      "total": 0
    },
    "login": {
      "total": 18,
      "success_count": 16,
      "fail_count": 2,
      "by_success": [
        { "key": "true",  "label": "成功", "count": 16 },
        { "key": "false", "label": "失败", "count": 2 }
      ],
      "by_type": [
        { "key": "jwt",      "label": "账号密码", "count": 14 },
        { "key": "sso_mock", "label": "SSO Mock", "count": 4 }
      ],
      "by_hour": [
        { "key": "8",  "label": "08:00", "count": 3 },
        { "key": "9",  "label": "09:00", "count": 5 },
        { "key": "14", "label": "14:00", "count": 10 }
      ]
    },
    "operation": {
      "total": 4,
      "by_action": [
        { "key": "create", "label": "新建", "count": 2 },
        { "key": "update", "label": "编辑", "count": 1 },
        { "key": "delete", "label": "删除", "count": 1 }
      ],
      "by_target_type": [
        { "key": "user",     "label": "用户", "count": 4 },
        { "key": "resource", "label": "资源", "count": 0 },
        { "key": "category", "label": "分类", "count": 0 }
      ],
      "top_operators": [
        { "key": "1", "label": "admin", "count": 3 },
        { "key": "2", "label": "root",  "count": 1 }
      ]
    },
    "online": {
      "online_users": 12,
      "window_minutes": 5,
      "as_of": "2026-07-06T16:48:00+08:00"
    }
  }
}
```

---

## 8. 前端取数建议（顶部 6 个统计卡片）

```typescript
interface GroupCount { key: string; label: string; count: number; }

interface DashboardOnline {
  online_users: number;
  window_minutes: number;
  as_of: string;
}

interface DashboardResponse {
  period: 'day' | 'week' | 'month';
  start_at: string;
  end_at: string;
  blocks: {
    upload:    any;
    query:     any;
    sensitive: any;
    login:     any;
    operation: any;
    online:    DashboardOnline | { error: string };
  };
}

// 卡片取数对照
const data = await fetchDashboard('day');

cards[0] = { title: '总上传',   value: data.blocks.upload.total };
cards[1] = { title: '总查询',   value: data.blocks.query.total  };
cards[2] = { title: '今日上传', value: data.blocks.upload.total };
cards[3] = { title: '今日查询', value: data.blocks.query.total  };
cards[4] = { title: '总存储量', value: formatBytes(data.blocks.upload.total_size) };
cards[5] = { title: '当前在线', value: data.blocks.online.online_users };

// 通用降级
function blockOk(b: any) { return b && !('error' in b); }
function blockValue(b: any, fallback = '—') {
  return blockOk(b) ? b : fallback;
}
```

---

## 9. 通用分组对象 `{key, label, count}` 约定

所有 `by_*` 数组都是同一结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | string | 数据库原始值（枚举值 / ID / 字符串键） |
| `label` | string | 给前端显示用的中文标签（后端已经翻译好） |
| `count` | integer | 该分组的条数 |

> 前端**不要**自己根据 `key` 二次翻译，统一直接渲染 `label`。

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-06 | 1.0 | 初版（5 个统计块） |
| 2026-07-06 | 1.1 | 新增 `blocks.online` 块（当前在线用户数，5 分钟窗口） |

---

## 11. 相关接口对照

| 用途 | 接口 |
|------|------|
| 顶部 6 卡片聚合（本次） | `GET /api/admin/logs/dashboard/` |
| 平台累计数字（不过滤周期） | 4 个 stats 接口（`/api/admin/logs/{upload,query,login,operation}/stats/`） |
| 单类日志列表 + 筛选 | 5 个列表接口（`/api/admin/logs/{upload,query,sensitive,login,operation}/`） |
| 单类日志详情 | 5 个详情接口（`/{id}/`） |