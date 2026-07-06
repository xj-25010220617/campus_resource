# admin_login_log 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-07-06
> **变更人员**：超管日志接口开发
> **变更原因**：登录行为审计 —— 需要记录 JWT 登录 / SSO 登录 / SSO Mock 登录 的 IP、UA、用户名、失败原因，给超管后台可查询
> **影响范围**：新建表，无现有数据影响
> **关联代码**：`backend/apps/admin_panel/models.py`（LoginLog, managed=False）
> **回滚方案**：`DROP TABLE IF EXISTS \`admin_login_log\`;`

```sql
CREATE TABLE `admin_login_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `user_id` BIGINT NULL COMMENT '登录用户 ID（NULL 表示失败且无法定位）',
    `username` VARCHAR(128) NOT NULL COMMENT '登录用户名（快照）',
    `login_type` VARCHAR(16) NOT NULL COMMENT '登录方式：jwt / sso / sso_mock',
    `success` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否成功：1=成功 / 0=失败',
    `fail_reason` VARCHAR(256) NOT NULL DEFAULT '' COMMENT '失败原因',
    `ip_address` VARCHAR(64) NULL COMMENT '客户端 IP',
    `user_agent` VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'User-Agent',
    `created_at` DATETIME(6) NOT NULL COMMENT '登录时间',

    INDEX `idx_loginlog_user_created` (`user_id`, `created_at`),
    INDEX `idx_loginlog_success_created` (`success`, `created_at`),
    INDEX `idx_loginlog_created` (`created_at`),
    INDEX `idx_loginlog_type_created` (`login_type`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='管理后台登录行为审计日志（由 Django model managed=False 远程手动建表）';
```

## 字段说明

| 字段 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 否 | — | 主键 |
| `user_id` | BIGINT | 是 | NULL | 登录用户 ID（NULL 表示未知用户） |
| `username` | VARCHAR(128) | 否 | — | 用户名快照（失败时也记录） |
| `login_type` | VARCHAR(16) | 否 | — | jwt / sso / sso_mock |
| `success` | TINYINT(1) | 否 | 0 | 1=成功 / 0=失败 |
| `fail_reason` | VARCHAR(256) | 否 | '' | 失败原因（如"凭证错误"、"无效 code"） |
| `ip_address` | VARCHAR(64) | 是 | NULL | 客户端 IP（兼容 IPv6） |
| `user_agent` | VARCHAR(512) | 否 | '' | User-Agent |
| `created_at` | DATETIME(6) | 否 | — | 登录时间 |

## 索引说明

| 索引 | 字段 | 用途 |
|------|------|------|
| PRIMARY | id | 主键 |
| idx_loginlog_user_created | (user_id, created_at) | 按用户查近期登录 |
| idx_loginlog_success_created | (success, created_at) | 失败登录筛查 |
| idx_loginlog_created | (created_at) | 全局时间范围 |
| idx_loginlog_type_created | (login_type, created_at) | 按登录方式分组 |

## 应用层

- Django 侧：`apps/admin_panel/models.py::LoginLog`，**`managed=False`**（由远程手动建表，Django 不参与 migration）
- 写入：`apps/admin_panel/utils.py::write_login_log` 工具函数，**失败仅记日志不抛异常**
- 触发点：
  - `backend/apps/accounts/urls.py` 的 `/api/auth/login/` → `LoggedTokenObtainPairView`
  - `backend/apps/accounts/views.py::SsoCallbackView._handle_mock_callback` 的成功 / 失败分支

> ⚠️ **注意**：因为使用 `managed=False`，**`python manage.py makemigrations` 不会生成此表的 migration 文件**。
> 若误操作，本表需要走回滚 SQL 后重建。
