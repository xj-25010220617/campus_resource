# 前端对接接口文档 — Admin 超管日志

> **日期**：2026-07-06
> **后端分支**：main（增量）
> **OpenAPI**：[docs/openapi-admin-logs-2026-07-06.json](./openapi-admin-logs-2026-07-06.json)
> **代码变更记录**：[code-changes/2026-07-06.md](../code-changes/2026-07-06.md)
> **数据库变更记录**：
> - [database-changes/admin_login_log.md](../database-changes/admin_login_log.md)
> - [database-changes/admin_operation_log.md](../database-changes/admin_operation_log.md)

> **认证说明**：所有接口均需要携带 JWT Token，格式：`Authorization: Bearer <token>`
> **权限**：仅 `super_admin` 角色可访问，其它角色返回 403
> **OpenAPI 鉴权兼容**：[docs/openapi-admin-logs-2026-07-06.json](./openapi-admin-logs-2026-07-06.json) 文件**未**使用 `components.securitySchemes` 段（避免 Apifox 2.8.21 解析失败）。鉴权方式以 description 文字 + Apifox「运行配置-Header」显式 `Authorization: Bearer {{token}}` 实现。

---

## 〇、本批接口清单（15 个）

| 类型 | 数量 | 路径前缀 |
|------|------|---------|
| 列表 + 详情 | 10 个 | `/api/admin/logs/{upload,query,sensitive,login,operation}/` + `/<id>/` |
| 统计 | 4 个 | `/api/admin/logs/{upload,query,login,operation}/stats/` |
| Dashboard 聚合 | 1 个 | `/api/admin/logs/dashboard/` |
| **合计** | **15** | — |

> 敏感内容（sensitive）不单独提供 stats 接口，仅出现在 Dashboard 中。

---

## 一、上传日志（复用现有 query）

### 1.1 列表

```
GET /api/admin/logs/upload/
```

**权限**：super_admin

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | integer | 否 | 页码，默认 1 |
| page_size | integer | 否 | 每页数量，默认 20 |
| user_id | integer | 否 | 上传用户 ID |
| status | string | 否 | `pending` / `success` / `failed` |
| college_id | integer | 否 | 学院 ID |
| department_id | integer | 否 | 部门 ID |
| file_type | string | 否 | `pdf/doc/ppt/xls/image/video/audio/other` |
| date_from | string | 否 | 起始日期 `YYYY-MM-DD` 或 ISO |
| date_to | string | 否 | 结束日期 `YYYY-MM-DD` 或 ISO |
| keyword | string | 否 | 按 `file_name` 模糊匹配 |

**响应示例**（HTTP 200）：

```json
{
  "count": 42,
  "next": "http://localhost:8000/api/admin/logs/upload/?page=2",
  "previous": null,
  "results": [
    {
      "id": 12,
      "user": 5,
      "user_name": "zhangsan",
      "resource": 1023,
      "resource_title": "操作系统课件.pdf",
      "file_name": "操作系统课件.pdf",
      "file_size": 524288,
      "file_type": "pdf",
      "college": 1,
      "college_name": "计算机学院",
      "status": "success",
      "error_message": "",
      "ip_address": "10.0.5.42",
      "user_agent": "Mozilla/5.0 ...",
      "created_at": "2026-07-06T03:21:11Z",
      "completed_at": "2026-07-06T03:21:13Z"
    }
  ]
}
```

### 1.2 详情

```
GET /api/admin/logs/upload/{id}/
```

**路径参数**：`id` integer

**响应**：单个 UploadLog 对象（结构同上 results 中的一项）

### 1.3 统计

```
GET /api/admin/logs/upload/stats/
```

**响应示例**（HTTP 200）：

```json
{
  "total": 1240,
  "today": 8,
  "this_week": 56,
  "this_month": 320,
  "by_status": [
    {"key": "success", "label": "成功", "count": 1180},
    {"key": "failed",  "label": "失败", "count": 50},
    {"key": "pending", "label": "处理中", "count": 10}
  ],
  "by_file_type": [
    {"key": "pdf",   "label": "pdf",   "count": 600},
    {"key": "doc",   "label": "doc",   "count": 320},
    {"key": "image", "label": "image", "count": 180}
  ],
  "by_college": [
    {"key": "1", "label": "计算机学院", "count": 420},
    {"key": "2", "label": "智能科学学院", "count": 280}
  ],
  "total_size": 524288000
}
```

> `by_college` 返回前 10 学院；字段含义：上传分布 + 体积。

---

## 二、查询日志

### 2.1 列表

```
GET /api/admin/logs/query/
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| user_id | integer | 用户 ID |
| user_feedback | integer | `1`=赞 / `0`=踩 / `-1`=无反馈 |
| hit_count_min | integer | 召回命中下限（≥N） |
| date_from | string | 同上 |
| date_to | string | 同上 |
| keyword | string | 按 `question` 模糊匹配 |

**响应示例**：

```json
{
  "count": 128,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 5,
      "user": 12,
      "user_name": "lisi",
      "conversation": 100,
      "question": "大三下操作系统复习资料",
      "parsed_filters": {"college_id": 1, "discipline_id": 1},
      "hit_count": 5,
      "top_doc_ids": [1023, 1045, 1089],
      "response_ms": 1850,
      "user_feedback": 1,
      "created_at": "2026-07-05T08:12:11Z"
    }
  ]
}
```

### 2.2 详情

```
GET /api/admin/logs/query/{id}/
```

### 2.3 统计

```
GET /api/admin/logs/query/stats/
```

**响应示例**：

```json
{
  "total": 1280,
  "today": 12,
  "this_week": 84,
  "this_month": 480,
  "avg_hit_count": 5.32,
  "avg_response_ms": 1450,
  "like_rate": 0.7143,
  "by_feedback": [
    {"key": "1",  "label": "赞",   "count": 914},
    {"key": "0",  "label": "踩",   "count": 78},
    {"key": "-1", "label": "无反馈", "count": 288}
  ]
}
```

---

## 三、敏感内容日志

### 3.1 列表

```
GET /api/admin/logs/sensitive/
```

**查询参数**：`user_id` / `date_from` / `date_to` / `keyword`

**响应示例**：

```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 22,
      "user": 8,
      "user_name": "wangwu",
      "question": "原文...",
      "matched_rules": [
        {"rule": "绝密", "desc": "涉密关键词：绝密", "type": "classified", "category": "涉密保密关键词"}
      ],
      "matched_texts": ["绝密"],
      "refuse_message": "抱歉...",
      "created_at": "2026-07-06T01:34:00Z"
    }
  ]
}
```

### 3.2 详情

```
GET /api/admin/logs/sensitive/{id}/
```

> 敏感内容**没有** stats 接口——Dashboard 中也有其汇总（仅 total 计数）。

---

## 四、登录日志

### 4.1 列表

```
GET /api/admin/logs/login/
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| user_id | integer | — |
| username | string | 按用户名模糊匹配 |
| login_type | string | `jwt` / `sso` / `sso_mock` |
| success | boolean | `true`=成功 / `false`=失败 |
| date_from | string | — |
| date_to | string | — |

**响应示例**：

```json
{
  "count": 56,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 89,
      "user": 1,
      "user_name": "admin",
      "username": "admin",
      "login_type": "jwt",
      "login_type_display": "账号密码登录",
      "success": true,
      "fail_reason": "",
      "ip_address": "10.0.5.42",
      "user_agent": "Mozilla/5.0 ...",
      "created_at": "2026-07-06T08:30:00Z"
    },
    {
      "id": 90,
      "user": null,
      "user_name": null,
      "username": "hacker",
      "login_type": "jwt",
      "login_type_display": "账号密码登录",
      "success": false,
      "fail_reason": "凭证错误或账号不存在",
      "ip_address": "203.0.113.7",
      "user_agent": "curl/7.81.0",
      "created_at": "2026-07-06T08:31:14Z"
    }
  ]
}
```

### 4.2 详情

```
GET /api/admin/logs/login/{id}/
```

### 4.3 统计

```
GET /api/admin/logs/login/stats/
```

**响应示例**：

```json
{
  "total": 1240,
  "today": 18,
  "this_week": 124,
  "this_month": 612,
  "success_count": 1180,
  "fail_count": 60,
  "by_success": [
    {"key": "true",  "label": "成功", "count": 1180},
    {"key": "false", "label": "失败", "count": 60}
  ],
  "by_type": [
    {"key": "jwt",      "label": "账号密码", "count": 980},
    {"key": "sso_mock", "label": "SSO Mock", "count": 200},
    {"key": "sso",      "label": "SSO",      "count": 60}
  ],
  "by_hour": [
    {"key": "8",  "label": "08:00", "count": 80},
    {"key": "9",  "label": "09:00", "count": 120}
  ]
}
```

> `by_hour` 为登录发生的小时分布，0-23 共 24 个桶（无事件的桶不出现在结果中）。

---

## 五、操作日志

### 5.1 列表

```
GET /api/admin/logs/operation/
```

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| operator_id | integer | 操作人 ID |
| action | string | `create` / `update` / `delete` / `reset_password` / `batch_delete` / `batch_reset_password` |
| target_type | string | `user` / `resource` / `category` |
| target_id | integer | 对象 ID |
| date_from | string | — |
| date_to | string | — |

**响应示例**：

```json
{
  "count": 32,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 102,
      "operator": 1,
      "operator_name": "admin",
      "operator_username": "admin",
      "action": "create",
      "action_display": "新建",
      "target_type": "user",
      "target_type_display": "用户",
      "target_id": 50,
      "target_repr": "testuser（普通用户）",
      "after": {
        "id": 50,
        "username": "testuser",
        "email": "test@example.com",
        "role": "user",
        "college": 1
      },
      "ip_address": "10.0.5.42",
      "user_agent": "Mozilla/5.0 ...",
      "created_at": "2026-07-06T03:11:00Z"
    },
    {
      "id": 103,
      "operator": 1,
      "operator_name": "admin",
      "operator_username": "admin",
      "action": "batch_delete",
      "action_display": "批量删除",
      "target_type": "user",
      "target_type_display": "用户",
      "target_id": null,
      "target_repr": "批量删除 5 个用户",
      "after": null,
      "ip_address": "10.0.5.42",
      "user_agent": "Mozilla/5.0 ...",
      "created_at": "2026-07-06T04:22:33Z"
    }
  ]
}
```

### 5.2 详情

```
GET /api/admin/logs/operation/{id}/
```

### 5.3 统计

```
GET /api/admin/logs/operation/stats/
```

**响应示例**：

```json
{
  "total": 86,
  "today": 4,
  "this_week": 22,
  "this_month": 86,
  "by_action": [
    {"key": "create",      "label": "新建",         "count": 30},
    {"key": "update",      "label": "编辑",         "count": 22},
    {"key": "delete",      "label": "删除",         "count": 10},
    {"key": "reset_password","label": "重置密码",   "count": 18},
    {"key": "batch_delete","label": "批量删除",     "count": 6}
  ],
  "by_target_type": [
    {"key": "user",     "label": "用户", "count": 86},
    {"key": "resource", "label": "资源", "count": 0},
    {"key": "category", "label": "分类", "count": 0}
  ],
  "top_operators": [
    {"key": "1", "label": "admin",       "count": 70},
    {"key": "2", "label": "super_admin", "count": 16}
  ]
}
```

> `top_operators` 返回前 10 名；目前 project 里 Resource/Category 暂无 CRUD，所以这两块值恒为 0——等后续该接口落地后会自动有数据。

---

## 六、Dashboard 聚合

```
GET /api/admin/logs/dashboard/?period=day|week|month
```

**权限**：super_admin

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| period | string | `day` | `day`=今天 / `week`=本周（周一 0 点 ~ 现在）/ `month`=本月（1 日 0 点 ~ 现在） |

时区按 settings.TIME_ZONE = `Asia/Shanghai`。

**响应示例**（HTTP 200，period=day）：

```json
{
  "period": "day",
  "start_at": "2026-07-06T00:00:00+08:00",
  "end_at": "2026-07-06T11:32:11+08:00",
  "blocks": {
    "upload": {
      "total": 8,
      "total_size": 4194304,
      "by_status": [{"key": "success", "label": "成功", "count": 8}],
      "by_file_type": [],
      "by_college": []
    },
    "query": {
      "total": 12,
      "avg_hit_count": 4.5,
      "avg_response_ms": 1620,
      "like_rate": 0.667,
      "by_feedback": []
    },
    "sensitive": {
      "total": 0
    },
    "login": {
      "total": 18,
      "success_count": 16,
      "fail_count": 2,
      "by_success": [...],
      "by_type": [...],
      "by_hour": [...]
    },
    "operation": {
      "total": 4,
      "by_action": [...],
      "by_target_type": [...],
      "top_operators": [...]
    }
  }
}
```

### 失败隔离语义

5 个块的计算**各自独立** try/except。一个块出错时：

- 该块返回 `{"error": "异常原因"}`
- 其它块仍然返回正常结构

前端渲染时建议：

```js
const blocks = data.blocks;
for (const [name, b] of Object.entries(blocks)) {
  if (b.error) {
    // 显示 "数据获取失败"
  } else {
    // 正常渲染
  }
}
```

---

## 七、公共说明

### 7.1 错误响应格式

```json
{
  "detail": "错误描述信息"
}
```

### 7.2 常见 HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 请求成功 |
| 401 | 未认证（Token 缺失 / 无效 / 过期） |
| 403 | 无权限（非 super_admin 角色） |
| 404 | 资源不存在 |

### 7.3 分页说明

- 所有列表接口使用 DRF 默认 `PageNumberPagination`，`PAGE_SIZE=20`
- 请求参数：`page`、`page_size`（page_size 不超过 100）
- 响应：`{count, next, previous, results}`

### 7.4 时区

- 所有时间字段（`created_at` / `completed_at` / `start_at` / `end_at`）均为 ISO 8601 格式，**带时区**
- 服务端时区：`Asia/Shanghai`（来自 `settings.TIME_ZONE`）

---

## 八、前端调用示例

### JavaScript (fetch)

```javascript
const token = localStorage.getItem('access');

// 1. 上传日志列表
const r1 = await fetch('/api/admin/logs/upload/?status=failed&date_from=2026-07-01', {
  headers: { 'Authorization': 'Bearer ' + token }
});
const uploadLogs = await r1.json();

// 2. 登录失败统计
const r2 = await fetch('/api/admin/logs/login/stats/', {
  headers: { 'Authorization': 'Bearer ' + token }
});
const loginStats = await r2.json();

// 3. Dashboard 聚合（本周）
const r3 = await fetch('/api/admin/logs/dashboard/?period=week', {
  headers: { 'Authorization': 'Bearer ' + token }
});
const dashboard = await r3.json();
```

### Vue 3 (axios)

```javascript
import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Authorization': `Bearer ${token}` }
});

// 上传日志列表（带筛选）
async function fetchUploadLogs(params) {
  const { data } = await api.get('/admin/logs/upload/', { params });
  return data.results;
}

// Dashboard 切换 period
async function fetchDashboard(period = 'day') {
  const { data } = await api.get('/admin/logs/dashboard/', {
    params: { period }
  });
  return data;  // { period, start_at, end_at, blocks }
}

// 列表请求模板
async function listLogs(endpoint, filter = {}) {
  const { data } = await api.get(endpoint, {
    params: { page: 1, page_size: 20, ...filter }
  });
  return data;
}

// 用法
const failedUploads = await listLogs('/admin/logs/upload/', { status: 'failed' });
const dashboard = await fetchDashboard('week');
```

### 注意点

- **token 过期**：登录接口返回 401 时，需跳转到登录页（`/login`）
- **权限不够**：登录但角色不是 super_admin 时，本批接口统一返回 403，前端应隐藏"管理后台-日志管理"菜单
- **dashboard 块失败**：前端要兼容 `block.error` 场景，展示"该块数据获取失败"

---

## 九、Apifox 导入步骤

### 步骤 1：导入 OpenAPI 文件

1. 打开 Apifox → 项目设置 → 导入数据 → OpenAPI/Swagger
2. 选择 [docs/openapi-admin-logs-2026-07-06.json](./openapi-admin-logs-2026-07-06.json)
3. 导入模式选择：**覆盖**或**追加**（建议「新增目录」）

### 步骤 2：配置环境 + Header（手动设置 token）

> **为什么不靠 securitySchemes 自动**：本 OpenAPI 文件**显式省略** `components.securitySchemes`，以适配 Apifox 2.8.21 的解析器（详见 [§九-附](#附-apifox-2-8-21--openapi-兼容性说明)）。

1. 进入「环境管理」，新建环境：
   - 环境名：`本地开发`
   - 基础 URL：`http://localhost:8000`
   - 前缀（如团队约定）：`/api`
2. 在「运行配置」里**手动**新增公共 Header：
   ```
   Authorization: Bearer {{token}}
   Content-Type: application/json
   ```
   下面登录脚本会自动写入 `{{token}}`

### 步骤 3：登录脚本（自动化获取 token）

在 `/api/auth/login` 的「后置脚本」里写入：

```javascript
// 解析登录响应，提取 access token
const resp = pm.response.json();
if (resp.access) {
  pm.environment.set('token', resp.access);
  pm.environment.set('refresh_token', resp.refresh);
  pm.test('Status code is 200', () => pm.expect(pm.response.code).to.equal(200));
}
```

### 步骤 4：测试每组接口

| 测试组 | 操作 |
|--------|------|
| **5 类日志列表** | 调 5 个 `/api/admin/logs/{type}/` 端点，期望 200 + 分页结果 |
| **5 类日志详情** | 任取列表中的 id，调 `/<id>/` 端点，期望 200 |
| **4 类 stats** | 调 4 个 `/stats/` 端点，期望 200 + 分组数据 |
| **Dashboard** | 调 `/dashboard/?period=day` 等三组，期望 200 |
| **权限校验** | 用 **非 super_admin** token 调任一日志接口，期望 **403** |
| **未认证** | 不带 `Authorization`，期望 **401** |

### 步骤 5：测试用例示例

#### Test 1: Dashboard 周期切换

```
1. 调用 GET /api/admin/logs/dashboard/?period=day
   期望：200，blocks.upload.total >= 0
2. 调用 GET /api/admin/logs/dashboard/?period=week
   期望：200，blocks.query.like_rate ∈ [0,1]
3. 调用 GET /api/admin/logs/dashboard/?period=month
   期望：200，blocks.login.by_hour 包含 24 个桶或子集
```

#### Test 2: 登录日志筛选

```
1. 调用 GET /api/admin/logs/login/?success=false
   期望：200，results 中每个 success 字段均为 false
2. 调用 GET /api/admin/logs/login/?login_type=jwt
   期望：200，results 中 login_type=jwt
3. 调用 GET /api/admin/logs/login/?username=admin
   期望：200，user_name 或 username 含 'admin'
```

#### Test 3: 权限校验

```
1. 用 admin_token 调 GET /api/admin/logs/upload/
   期望：403
2. 用 super_admin_token 调 GET /api/admin/logs/upload/
   期望：200
```

### 步骤 6：导出用例

在「自动化测试」面板，配置运行计划，导出为 `postman collection` / `Junit XML` / `html 报告`，或接入 CI。

---

## 十、与已有接口的关系

本批上传日志接口（`/api/admin/logs/upload/`）与项目内已有的上传日志接口（`/api/resources/upload-log/`）**共用同一张 `UploadLog` 表**，但用途、权限、范围都不同。

### 10.1 完整对比表

| 维度 | `/api/resources/upload-log/`（已有） | `/api/admin/logs/upload/`（本批新增） |
|------|--------------------------------------|----------------------------------------|
| **模块归属** | `apps/resources/views.py::UploadLogViewSet`（资源模块） | `apps/admin_panel/views.py::AdminUploadLogListView`（管理面板模块） |
| **权限** | `IsAnyAdmin`：`super_admin` / `college_admin` / `dept_admin` / 兼容 `admin` 均可 | `IsAuthenticated + IsSuperAdmin`：**仅 super_admin** |
| **可见范围** | **按角色自动收紧**（核心差异）：<br>• `super_admin` → 平台全部<br>• `college_admin` / `admin` → 本学院 + 未分配部门<br>• `dept_admin` → 本部门 + 未分配学院<br>• 其他角色 → 无权限 | **不过滤范围**，全量可见（既已是超管） |
| **用途定位** | 学院 / 部门管理员看「自己学院 / 部门发生了什么上传」 | 超管看「平台整体上传态势」 |
| **路由风格** | DRF Router + `ReadOnlyModelViewSet` | `ListAPIView` / `RetrieveAPIView`（不用 Router） |
| **额外接口** | • `GET /stats/`：含 `group_by=...` 维度参数（status/file_type/college/user）<br>• `PATCH /{id}/complete/`：上传后端回调入口 | • `GET /stats/`：固定 4 个分组 + total/today/week/month + total_size（**无 group_by**）<br>• 无 `/complete/`（不走此接口完成上传） |
| **筛选参数** | `status` / `file_type` / `college` / `department` / `start_date` / `end_date` / `user_id` / `college_id` / `search` / `ordering` | `status` / `user_id` / `college_id` / `department_id` / `file_type` / `date_from` / `date_to` / `keyword`（命名统一为 `date_from`） |
| **排序** | 默认 `created_at` 倒序，可通过 `ordering=-file_size` 等切换 | **强制 `-created_at` 倒序**（不可改） |
| **分页** | DRF 默认 20/页 + `page_size` 可调 | DRF 默认 20/页 + `page_size` 可调（一致） |
| **响应体** | `{count, next, previous, results}` 分页容器 | 同上，一致 |

### 10.2 关键差异（一句话）

> **学院 / 部门管理员**用 `/api/resources/upload-log/`（系统已经帮你过滤好本学院 / 部门范围）；
> **超管**用 `/api/admin/logs/upload/`（一个接口看全平台，没有被范围过滤）。

### 10.3 为什么不在已有接口上给超管放开

已有接口的 `scope_upload_logs()` 是写死的「按角色收紧」。修改它会影响所有管理员的安全过滤——会被范围破坏。**新接口**才是给超管用的「无过滤全量入口」，而不是给旧接口开后门。

### 10.4 谁能用两套

只有 `super_admin` —— `scope_upload_logs` 在 super_admin 分支直接返回原 qs，所以超管访问**两套接口都能返回全量**。一般场景下统一走超管接口。

### 10.5 边界：是否给 college_admin / dept_admin 升级到全平台视图

当前架构答案：**不能**。他们的接口就是被收紧到本学院 / 本部门，这是产品决策。如果以后想给学院管理员升级到超管视角，应走**权限升级到 super_admin**，而不是给旧接口开后门。

### 10.6 前端路由判断与参数映射示例

```javascript
const user = useAuth().user;

// 路由判断：哪个接口
const endpoint = user.role === 'super_admin'
  ? '/admin/logs/upload/'      // 新接口，全量
  : '/resources/upload-log/';  // 旧接口，本学院 / 本部门

// 同一份组件，参数映射
const filters = ref({
  status: '',
  fileType: '',
  dateFrom: '',   // 新接口用 date_from，旧接口用 start_date
  dateTo: '',     // 新接口用 date_to，旧接口用 end_date
  keyword: '',    // 新接口用 keyword（仅 file_name）
});

const queryParams = user.role === 'super_admin'
  ? {
      status: filters.value.status,
      file_type: filters.value.fileType,
      date_from: filters.value.dateFrom,
      date_to: filters.value.dateTo,
      keyword: filters.value.keyword,
    }
  : {
      status: filters.value.status,
      file_type: filters.value.fileType,
      start_date: filters.value.dateFrom,
      end_date: filters.value.dateTo,
      search: filters.value.keyword,         // 旧接口还能按 username 搜
    };
```

### 10.7 其他类别一览

| 类别 | 现有接口 | 本批接口 | 关系 |
|------|---------|---------|------|
| 上传日志 | `GET /api/resources/upload-log/`（各角色看自己范围） | `GET /api/admin/logs/upload/`（超管全量） | 并存，前端按角色选择 |
| query_log / sensitive_log | 无管理接口 | 本批新增超管接口 | 全新 |
| 登录审计 | 无（仅应用 `LoginLog` 表） | 本批新增（`LoginLog` 自动埋点） | 全新 |
| 操作审计 | 无（仅应用 `OperationLog` 表） | 本批新增（`OperationLog` 自动埋点） | 全新 |
| 用户管理 | `GET /api/admin/users/` 等 | 无 | 已有 |
| Dashboard 聚合 | 无 | `GET /api/admin/logs/dashboard/` | 全新 |

---

## 十一、未覆盖（未来扩展）

| 路径扩展点 | 影响 |
|-----------|------|
| `GET /api/admin/logs/resource/` / `category/` | 等 Resource/Category CRUD 入口落地后，由 `OperationLogMixin` 自动接管 |
| `GET /api/admin/logs/resource/stats/` | 同上 |
| `POST /api/admin/logs/{type}/export/` | 导出 Excel / CSV（V2） |
| `GET /api/admin/logs/{type}/realtime/` | 实时大屏推送（V2，SSE） |

---

## 十二、变更记录

| 版本 | 日期 | 改动 |
|------|------|------|
| 1.0 | 2026-07-06 | 首版：15 个新接口 + 2 张审计表 + LoginLog/OperationLog 自动埋点 |

---

## 附：Apifox 2.8.21 与 OpenAPI 兼容性说明

初次导入时报「数据格式不正确」是因为以下字段被 Apifox 2.8.21 解析器拒绝，本批文件已**精简至无风险字段**：

| 不被接受的字段 | 处理方式 | 备注 |
|--------------|---------|------|
| `components.securitySchemes` | 整段移除 | 改用 Apifox 环境 Header 配 token |
| `components.schemas` + `$ref` | 全部内联到 `paths.*.responses` | 共 17 个 schema 全部展开 |
| `oneOf` (3.0.x 草稿需求) | 移除 | Dashboard `blocks.*` 用 `type:object` 兜底 |
| `nullable: true` (3.0.x 草稿) | 移除 | 不响应/外键为 0 仍能描述，不丢失语义 |
| `enum: [-1, 0, 1]`（非字符串 enum） | 改为 `enum: ["1", "0", "-1"]` | Apifox 接受字符串 enum |
| 顶层 `security` 段落 | 移除 | 与 securitySchemes 配合使用，单独保留没意义 |

**判断依据**：团队已有的 `docs/openapi-admin-extend-2026-07-02.json` 已能被 Apifox 导入，本次文件采用了同一份极简模板（仅 `openapi/info/servers/paths`）。

如未来 Apifox 升级后支持新字段，可在 doc-only 模式下保留 `$ref` 复用（不影响前端对接文档）。
