# 接口文档 - Admin Me & Departments

> 生成时间：2026-07-03
>
> **认证说明**：所有接口均需要携带 JWT Token，格式：`Authorization: Bearer <token>`

---

## 一、接口概览

| 方法 | 路径 | 功能 | 权限 |
|------|------|------|------|
| GET | `/api/admin/me/` | 当前登录用户信息（含管理范围） | 登录用户 |
| GET | `/api/admin/departments/` | 部门列表（树形结构） | 仅管理员 |

---

## 二、GET /api/admin/me/

**功能**：获取当前登录用户的归属与权限范围。

**权限**：登录用户（含 `role=user`）。普通用户也会返回 `permissions`，用于前端决定按钮可见性。

### 查询参数

无

### 响应示例

```json
{
  "id": 12,
  "username": "zhang_san",
  "email": "zhang@example.com",
  "role": "college_admin",
  "role_display": "学院管理员",
  "is_staff": false,
  "is_superuser": false,
  "college_id": 2,
  "college_name": "计算机学院",
  "department_id": null,
  "department_name": null,
  "permissions": {
    "manage_users": true,
    "manage_resources": true,
    "manage_scope": "college"
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 用户主键 |
| `username` | string | 用户名 |
| `email` | string \| null | 邮箱 |
| `role` | string | 见下表 `role` 取值 |
| `role_display` | string | 角色中文显示名 |
| `is_staff` | bool | Django 原生 staff 标志 |
| `is_superuser` | bool | Django 原生 superuser 标志 |
| `college_id` | integer \| null | 所属学院 ID；部门管理员 / 校级用户为 null |
| `college_name` | string \| null | 所属学院名称 |
| `department_id` | integer \| null | 所属部门 ID；学院管理员 / 学生为 null |
| `department_name` | string \| null | 所属部门名称 |
| `permissions.manage_users` | bool | 是否可进入用户管理 |
| `permissions.manage_resources` | bool | 是否可进入资源管理 |
| `permissions.manage_scope` | string | 见下表 `manage_scope` 取值 |

### `role` 取值

| 值 | 中文 | 说明 |
|----|------|------|
| `super_admin` | 超级管理员 | 看到全部 |
| `college_admin` | 学院管理员 | 只看本学院 role=user 的学生 |
| `dept_admin` | 部门管理员 | 只看本部门所有 user |
| `admin` | 管理员（兼容旧角色） | 行为同 `college_admin` |
| `user` | 普通用户 | 学生 / 员工，仅可使用 AI 问答 |

### `permissions.manage_scope` 取值

| 值 | 含义 |
|----|------|
| `all` | super_admin，无范围限制 |
| `college` | 学院管理员，仅本学院 |
| `department` | 部门管理员，仅本部门 |
| `self` | 非管理员，仅自己 |

### 错误码

| 状态码 | 场景 |
|--------|------|
| 401 | 未登录或 token 失效 |
| 405 | 用错 HTTP 方法 |

### 前端使用建议

- **缓存位置**：放 `pinia / zustand / redux`，**不要放 localStorage**
- **缓存 TTL**：5 分钟，或路由切换时 invalidate
- **刷新时机**：登录成功、退出登录、token 续期成功
- **与 `/api/auth/me/` 区别**：本接口额外返回 `permissions` 与归属字段，前端做权限判断请只调这一个

---

## 三、GET /api/admin/departments/

**功能**：获取所有部门（一级 + 子级）的树形结构，用于级联选择 / 部门管理页。

**权限**：仅管理员（`role ∈ {super_admin, college_admin, dept_admin, admin}`）。`role=user` 调此接口会 **403**。

**不分页**：一次返回全量。

### 查询参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| college_id | integer | 否 | 无 | 按所属学院筛选。**当前 Department 模型尚未挂 `college` 字段，传此参数暂不起作用**（见下文「⚠️ 模型限制」）。 |

### 响应示例

```json
[
  {
    "id": 1,
    "name": "校级领导",
    "parent": null,
    "sort_order": 1,
    "children": [
      {
        "id": 11,
        "name": "党委办公室",
        "parent": 1,
        "sort_order": 1,
        "children": []
      },
      {
        "id": 12,
        "name": "校长办公室",
        "parent": 1,
        "sort_order": 2,
        "children": []
      }
    ]
  },
  {
    "id": 2,
    "name": "教务处",
    "parent": null,
    "sort_order": 2,
    "children": []
  }
]
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 部门主键 |
| `name` | string | 部门名称（最长 128） |
| `parent` | integer \| null | 上级部门 ID；一级部门为 null |
| `sort_order` | integer | 排序值，越小越靠前 |
| `children` | array | 子部门列表，结构同父节点；无子部门时为 `[]` |

### 排序规则

`ORDER BY sort_order ASC, id ASC`

### 错误码

| 状态码 | 场景 |
|--------|------|
| 401 | 未登录或 token 失效 |
| 403 | 已登录但非管理员（`role=user` 等） |
| 405 | 用错 HTTP 方法 |

### 前端使用建议

- **缓存位置**：内存（pinia / redux）
- **缓存 TTL**：30 分钟（部门不常变）
- **失效时机**：在 `/api/admin/users/` 新建/删除用户导致部门绑定变化时刷新；或收到 403 时降级提示
- **与 `/api/departments/` 区别**：该路由在 `apps/classify` 下，是基础版（也是仅管理员）。**前端建议只对接 `/api/admin/departments/` 这一个**，与 `/api/admin/colleges/` 配对

### ⚠️ 模型限制

| 模型字段 | 当前状态 | 后续计划 |
|----------|----------|----------|
| `id` / `name` / `parent` / `sort_order` | ✅ 已存在 | — |
| `college` (FK → College) | ❌ **不存在** | 待加：把部门挂到学院，使 `?college_id=` 过滤生效 |

"先选学院再选部门"的级联方案：

1. **临时方案（无需后端改动）**：拉全部部门，前端用 `college_id` 做客户端过滤
2. **正式方案**：等后端补 `Department.college` + 迁移脚本 + 接口支持 `?college_id=` 后，走真实筛选

---

## 四、关联接口

| 用途 | 路径 |
|------|------|
| 修改密码 | `POST /api/auth/change-password/` |
| 学院列表（管理员） | `GET /api/admin/colleges/` |
| 用户管理（管理员） | `GET /api/admin/users/` |
| 资源列表 | `GET /api/resources/` |

---

## 五、OpenAPI 描述文件

完整 OpenAPI 3.0.3 JSON：
- `docs/openapi-admin-me-departments.json`
