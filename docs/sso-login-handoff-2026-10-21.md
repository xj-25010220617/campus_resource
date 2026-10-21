# SSO 登录前后端现状与联调说明

- **日期**：2026-10-21
- **前端**：`vue-file-system`
- **后端**：`campus_resource`
- **当前测试服务**：`http://124.221.94.207:8080`（前端开发环境经 Vite 代理 `/api`）
- **目的**：说明 SSO 登录的当前实际行为、根因、前端已做的修改，以及后端需要配合的事项

---

## 1. 问题现象

登录页点击「统一身份认证登录」后：

1. 弹出「选择测试账号」弹窗；
2. 弹窗中固定显示两个账号：
   - `sso_college_admin` / 学院SSO管理员
   - `sso_dept_admin` / 部门SSO管理员
3. 选择任一账号后登录失败。

浏览器网络面板显示：

```
GET http://localhost:5173/api/auth/sso/login/
HTTP/1.1 501 Not Implemented
{"detail":"SSO 登录暂未配置，请联系管理员"}
```

---

## 2. 实测结果（绕过前端直连后端）

### 2.1 SSO 登录入口

```bash
curl -i http://124.221.94.207:8080/api/auth/sso/login/
```

```http
HTTP/1.1 501 Not Implemented
Allow: GET, HEAD, OPTIONS
Content-Type: application/json
Server: gunicorn
Via: 1.1 Caddy

{"detail":"SSO 登录暂未配置，请联系管理员"}
```

### 2.2 SSO 回调

```bash
curl -i -X POST http://124.221.94.207:8080/api/auth/sso/callback/ \
  -H 'Content-Type: application/json' \
  -d '{"code":"mock_college_admin"}'
```

```http
HTTP/1.1 501 Not Implemented
Allow: POST, OPTIONS
Content-Type: application/json
Server: gunicorn
Via: 1.1 Caddy

{"detail":"SSO 回调暂未对接"}
```

### 2.3 结论

- 请求已经正确到达 Django/gunicorn 后端，**不是前端地址拼错、Vite 代理或 CORS 问题**；
- `Allow: GET, HEAD, OPTIONS` / `Allow: POST, OPTIONS` 说明路由存在且视图已匹配，是视图主动返回 501；
- 两个接口同时 501，说明后端 SSO 模块整体处于未启用/未对接状态。

---

## 3. 后端现有 SSO 设计

### 3.1 模式开关

`backend/apps/accounts/views.py`：

```python
def _sso_mock_enabled() -> bool:
    """Mock SSO 安全门槛：仅在显式开启且处于 DEBUG 环境时可用。

    生产环境（DEBUG=False）无论 SSO_MOCK_ENABLED 如何配置都不放行，
    避免任何人用固定 code 换取高权限 JWT。
    """
    return bool(settings.DEBUG) and getattr(settings, 'SSO_MOCK_ENABLED', False)
```

`backend/config/settings.py`：

```python
DEBUG = config('DEBUG', default=False, cast=bool)
SSO_MOCK_ENABLED = config('SSO_MOCK_ENABLED', default=False, cast=bool)
SSO_LOGIN_URL = config('SSO_LOGIN_URL', default='')
```

因此只有 **`DEBUG=True` 且 `SSO_MOCK_ENABLED=True`** 时才启用 Mock SSO。

### 3.2 Mock 模式

`GET /api/auth/sso/login/`（`SsoLoginView`）在 Mock 开启时返回：

```json
{
  "mode": "mock",
  "mock_codes": [
    {
      "code": "mock_super_admin",
      "username": "sso_super_admin",
      "role": "super_admin",
      "description": "超级管理员SSO（super_admin）"
    }
  ],
  "usage": "使用任一 code 调用 POST /api/auth/sso/callback/ 即可登录"
}
```

账号来源是后端常量 `MOCK_SSO_USERS`（`views.py` 约 116-167 行），共 6 个：

| code | username | role |
|---|---|---|
| `mock_super_admin` | `sso_super_admin` | `super_admin` |
| `mock_admin` | `sso_admin` | `admin` |
| `mock_college_admin` | `sso_college_admin` | `college_admin` |
| `mock_dept_admin` | `sso_dept_admin` | `dept_admin` |
| `mock_teacher` | `sso_teacher` | `user` |
| `mock_student` | `sso_student` | `user` |

`POST /api/auth/sso/callback/` 在 Mock 开启时：

1. 校验 `code` 是否属于 `MOCK_SSO_USERS`；
2. 按 `sso_uid` 查找本地用户，不存在则自动创建；
3. SSO 用户设置不可用密码；
4. 签发 SimpleJWT `access` / `refresh`，返回 `user`；
5. 写入 `admin_login_log`，`login_type=sso_mock`。

相关代码：`SsoCallbackView._handle_mock_callback()` / `_get_or_create_sso_user()`。

### 3.3 正式对接模式（尚未实现）

`SsoLoginView`：

- 配置了 `SSO_LOGIN_URL`：**返回 200 JSON** `{detail, sso_url}`（注意：`docs/API文档.md` 写的是 302，代码实际不是 302）；
- 未配置：返回 501 `{"detail":"SSO 登录暂未配置，请联系管理员"}`。

`SsoCallbackView`：

- 非 Mock 模式直接返回 501 `{"detail":"SSO 回调暂未对接"}`；
- 代码中仍是 `TODO: 对接学校 SAML/OAuth SSO 后实现实际校验逻辑`。

### 3.4 用户模型预留

`backend/apps/accounts/models.py` 的 `User` 已有：

```python
sso_uid = models.CharField(
    max_length=128, null=True, blank=True, unique=True,
    db_index=True, verbose_name='SSO统一ID',
)
```

正式对接时可用 `sso_uid` 做学校身份与本地用户的唯一映射。

### 3.5 当前线上为什么是 501

- `_sso_mock_enabled()` 要求 `DEBUG=True`；
- 线上实测 `DEBUG=False`（404 返回纯 HTML、未放开任意 Origin 的 CORS，符合 `DEBUG=False` 行为）；
- `SSO_LOGIN_URL` 未配置；
- 因此两个接口都走 501 分支。

这是后端 commit `a2b9049`（“security: SSO mock 仅限 DEBUG 环境，生产环境 fail-closed”）的安全加固结果，对应审计中的 P0-1“SSO Mock 后门”。

---

## 4. 前端现状

### 4.1 修改前（旧版本）为什么会出现两个账号

旧代码在 `src/views/login/LoginPage.vue` 的 `handleSSOLogin()` 中：

```ts
const response = await fetch(url, { redirect: 'manual' })
if (response.status === 302) { ... }
if (response.ok) {
  const data = await response.json()
  if (data.mock_codes && Array.isArray(data.mock_codes)) {
    ssoAccounts.value = data.mock_codes
    // 弹窗
    return
  }
} catch { /* 忽略 */ }

// 后端不可用或未返回 mock_codes 时使用默认测试账号
ssoAccounts.value = [
  { code: 'mock_college_admin', username: 'sso_college_admin', role: 'college_admin', description: '学院SSO管理员' },
  { code: 'mock_dept_admin', username: 'sso_dept_admin', role: 'dept_admin', description: '部门SSO管理员' },
]
ssoDialogVisible.value = true
```

执行路径：

```
点击按钮
  → GET /api/auth/sso/login/
  → 后端 501
  → response.ok === false（fetch 对 4xx/5xx 不抛异常）
  → if (response.ok) 整块跳过
  → catch 不执行
  → 落到硬编码默认账号
  → 弹窗显示两个假账号
```

所以弹窗里的两个账号**不是后端返回的**，是前端写死的兜底数据，与后端 `MOCK_SSO_USERS` 也不一致。

### 4.2 修改后（当前工作区，尚未提交/部署）

已删除硬编码兜底，`handleSSOLogin()` 只根据后端返回处理：

| 后端响应 | 前端行为 |
|---|---|
| `200` + `mock_codes: [...]` | 原样赋值给 `ssoAccounts`，展示选择弹窗 |
| `200` + `sso_url: "..."` | `window.location.href = sso_url`，跳转统一认证 |
| `302`（可读 `Location`） | 跳转 `Location` |
| 浏览器 `opaqueredirect` | 重新访问 SSO 登录入口，由浏览器跟随重定向 |
| `501` / 其他非 2xx | 展示后端 `detail`，不弹账号框 |
| 网络异常 | 提示无法连接，不弹账号框 |

前端不再包含任何账号常量；`sso_college_admin`、`mock_college_admin` 等字符串不会再出现在运行时源码中。

**注意**：该修改目前只在工作区，尚未提交、未构建部署。仓库内 `dist/` 仍是旧构建产物，线上旧前端仍会显示硬编码账号，需重新构建部署后才会生效。

---

## 5. 前后端期望契约

### 5.1 `GET /api/auth/sso/login/`

| 场景 | 状态码 | 响应体 |
|---|---|---|
| Mock 可用 | 200 | `{ "mode": "mock", "mock_codes": [{ "code", "username", "role", "description" }] }` |
| 已配置真实 SSO | 200 | `{ "mode": "redirect", "sso_url": "https://..." }` |
| 未配置 | 501 | `{ "mode": "unconfigured", "detail": "SSO 登录暂未配置，请联系管理员" }` |

> 前端当前兼容不带 `mode` 字段的旧响应，只识别 `mock_codes` / `sso_url`；后端可以平滑增加 `mode`。
>
> 建议正式模式返回 200 `{sso_url}`，而不是依赖 302。原因是前端使用 `fetch(..., {redirect: 'manual'})` 时，浏览器跨域重定向会得到 `opaqueredirect`（status 0，读不到 `Location`）。如果后端坚持 302，前端已做 `opaqueredirect` 兼容，也可以工作。

### 5.2 `POST /api/auth/sso/callback/`

| 场景 | 状态码 | 响应体 |
|---|---|---|
| Mock 成功 | 200 | `{ "access", "refresh", "user" }` |
| 正式成功 | 200 | `{ "access", "refresh", "user" }` |
| code/票据无效 | 401 | `{ "detail": "...", "code": "..." }` |
| 未配置/未对接 | 501 | `{ "detail": "SSO 回调暂未对接" }` |

前端回调失败时会展示后端 `detail`，不再显示笼统的“SSO 登录失败”。

---

## 6. 后端需要配合的事项

### 方案 A：测试/演示环境恢复 Mock（当前阶段推荐）

目标：让测试服重新返回 `mock_codes`，前端按后端数据渲染账号。

**A1. `backend/config/settings.py` 增加环境标识**

```python
# production / test / development
ENVIRONMENT = config('ENVIRONMENT', default='development' if DEBUG else 'production')

SSO_MOCK_ENABLED = config('SSO_MOCK_ENABLED', default=False, cast=bool)
SSO_MOCK_ALLOW_PRIVILEGED = config('SSO_MOCK_ALLOW_PRIVILEGED', default=False, cast=bool)
SSO_LOGIN_URL = config('SSO_LOGIN_URL', default='')
```

**A2. `backend/apps/accounts/views.py` 调整开关**

```python
def _sso_mock_enabled() -> bool:
    """Mock SSO 仅在显式开启且非生产环境可用；生产 fail-closed。"""
    return (
        getattr(settings, 'ENVIRONMENT', 'production') in ('development', 'test')
        and bool(getattr(settings, 'SSO_MOCK_ENABLED', False))
    )
```

**A3. 测试服务器 `.env`**

```env
ENVIRONMENT=test
SSO_MOCK_ENABLED=True
SSO_MOCK_ALLOW_PRIVILEGED=False
```

生产服务器保持：

```env
ENVIRONMENT=production
SSO_MOCK_ENABLED=False
# 真实 SSO 对接后再配置 SSO_LOGIN_URL
```

**A4.（建议）测试服默认不暴露高权限 Mock 账号**

`MOCK_SSO_USERS` 包含 `mock_super_admin` / `mock_admin`。在公网测试服开启 Mock，等于重新引入审计 P0-1 的提权风险。建议 `SsoLoginView` 在 `SSO_MOCK_ALLOW_PRIVILEGED=False` 时过滤掉这两个角色，只返回：

- `mock_college_admin`
- `mock_dept_admin`
- `mock_teacher`
- `mock_student`

需要测试超管时再临时打开 `SSO_MOCK_ALLOW_PRIVILEGED=True`。

### 方案 B：正式对接学校 SSO

1. 配置 `SSO_LOGIN_URL`（学校统一认证地址）；
2. 实现 `SsoCallbackView` 的 SAML/OAuth 票据校验（验签、有效期、audience 等）；
3. 增加 `state` 参数并做一次性校验，防止 CSRF；
4. 按 `sso_uid` 查找本地用户：
   - 已存在：直接登录；
   - 不存在：创建用户，**默认角色必须是 `user`**；
   - 管理员角色由本系统后台分配，不信任 IdP 返回的角色属性；
5. 签发 JWT，并写入 `admin_login_log`（`login_type=sso`）；
6. 与前端约定回调落点：
   - 推荐：IdP → 前端 `/login?code=...&state=...` → 前端 `POST /api/auth/sso/callback/`；
   - 或 IdP → 后端回调地址 → 后端 302 回前端。

当前前端已支持 `?code=` 回调（`LoginPage.vue` 的 `onMounted`）；接入 `state` 时需要前后端一起补充参数。

---

## 7. 安全注意事项

1. **生产 fail-closed**：`ENVIRONMENT=production` 时 Mock 必须无效，即使 `SSO_MOCK_ENABLED` 误设为 `True`。
2. **不要在公网测试服默认暴露 `mock_super_admin`**，建议默认过滤高权限账号。
3. **正式 SSO 不按 IdP 属性自动授予管理员角色**，新用户默认 `user`。
4. **`state` 必须校验**，防止登录 CSRF。
5. **登录日志**：Mock 用 `sso_mock`，正式用 `sso`，成功/失败都记录。
6. **前端不需要也不应该保存任何账号映射**；账号列表只能来自后端。

---

## 8. 验收清单

### 后端

```bash
# 测试服：应返回 200 + mock_codes
curl -s http://124.221.94.207:8080/api/auth/sso/login/

# Mock 回调：应返回 access/refresh/user
curl -s -X POST http://124.221.94.207:8080/api/auth/sso/callback/ \
  -H 'Content-Type: application/json' \
  -d '{"code":"mock_college_admin"}'
```

- [ ] 测试服 `GET /api/auth/sso/login/` 返回 `200 {mode:"mock", mock_codes:[...]}`；
- [ ] `POST /api/auth/sso/callback/` 用返回的 code 能拿到 JWT；
- [ ] 生产环境（`ENVIRONMENT=production`）即使 `SSO_MOCK_ENABLED=True` 仍返回 501；
- [ ] 正式模式配置 `SSO_LOGIN_URL` 后返回可用的跳转地址；
- [ ] 登录日志记录 `sso_mock` / `sso`。

### 前端

- [ ] 点击「统一身份认证登录」后，弹窗账号与后端 `mock_codes` 完全一致；
- [ ] 前端源码/构建产物中不存在硬编码账号；
- [ ] 后端 501 时只显示错误提示，不出现账号弹窗；
- [ ] 正式模式点击后跳转学校统一认证页面，不出现账号弹窗。

---

## 9. 涉及文件

### 后端

| 文件 | 相关内容 |
|---|---|
| `backend/config/settings.py` | `DEBUG`、`SSO_MOCK_ENABLED`、`SSO_LOGIN_URL`、建议新增 `ENVIRONMENT` |
| `backend/apps/accounts/views.py` | `MOCK_SSO_USERS`、`_sso_mock_enabled()`、`SsoLoginView`、`SsoCallbackView` |
| `backend/apps/accounts/models.py` | `User.sso_uid` 唯一字段 |
| `backend/apps/accounts/urls.py` | `sso/login/`、`sso/callback/` 路由 |
| `docs/API文档.md` | SSO 接口文档（当前写 302，与代码 200 JSON 不一致，需统一） |

### 前端

| 文件 | 相关内容 |
|---|---|
| `src/views/login/LoginPage.vue` | `handleSSOLogin()` / `handleSSOSelect()` / `onMounted()` 的 `?code=` 回调 |
| `src/api/auth.ts` | `ssoLoginUrl()`、`ssoCallbackApi()` |
| `vite.config.ts` | `/api` 代理到 `VITE_API_PROXY` |
| `.env.development` | `VITE_API_URL=/api`、`VITE_API_PROXY=http://124.221.94.207:8080` |

---

## 10. 待确认问题

1. `124.221.94.207` 是测试/演示服还是准生产环境？
2. 是否允许在该服务器设置 `ENVIRONMENT=test`、`SSO_MOCK_ENABLED=True`？
3. 测试服是否默认隐藏 `mock_super_admin` / `mock_admin`？
4. 学校 SSO 的协议是 SAML 2.0 还是 OAuth2/OIDC？能否提供 IdP 元数据、回调地址要求和测试账号？
5. 正式 SSO 的回调落点确定为前端 `/login?code=...&state=...`，还是后端 `/api/auth/sso/callback/`？
6. `docs/API文档.md` 中 SSO 登录入口的 302 描述是否改为 200 JSON（或后端改为真正 302），需要前后端统一。
