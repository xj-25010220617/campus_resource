# admin_operation_log 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-07-06
> **变更人员**：超管日志接口开发
> **变更原因**：超管 CRUD 操作审计 —— 记录谁（operator）在什么时间（created_at）对什么对象（target_type + target_id）做了什么动作（action），含 after 快照
> **影响范围**：新建表，无现有数据影响
> **关联代码**：`backend/apps/admin_panel/models.py`（OperationLog, managed=False）
> **回滚方案**：`DROP TABLE IF EXISTS \`admin_operation_log\`;`

```sql
CREATE TABLE `admin_operation_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `operator_id` BIGINT NULL COMMENT '操作人 ID（NULL 表示匿名）',
    `operator_username` VARCHAR(128) NOT NULL COMMENT '操作人（快照）',
    `action` VARCHAR(32) NOT NULL COMMENT '动作：create/update/delete/reset_password/batch_delete/batch_reset_password',
    `target_type` VARCHAR(32) NOT NULL COMMENT '对象类型：user/resource/category',
    `target_id` BIGINT NULL COMMENT '对象 ID（NULL 表示批量）',
    `target_repr` VARCHAR(256) NOT NULL DEFAULT '' COMMENT '对象摘要',
    `after` JSON NULL COMMENT '操作后数据快照（UserAdminSerializer.data）',
    `ip_address` VARCHAR(64) NULL COMMENT '客户端 IP',
    `user_agent` VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'User-Agent',
    `created_at` DATETIME(6) NOT NULL COMMENT '操作时间',

    INDEX `idx_oplog_operator_created` (`operator_id`, `created_at`),
    INDEX `idx_oplog_target` (`target_type`, `target_id`),
    INDEX `idx_oplog_action_created` (`action`, `created_at`),
    INDEX `idx_oplog_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='管理后台超管 CRUD 操作审计日志（由 Django model managed=False 远程手动建表）';
```

## 字段说明

| 字段 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | BIGINT AUTO_INCREMENT | 否 | — | 主键 |
| `operator_id` | BIGINT | 是 | NULL | 操作人 ID |
| `operator_username` | VARCHAR(128) | 否 | — | 操作人用户名快照（防止用户被改名后无法回溯） |
| `action` | VARCHAR(32) | 否 | — | 见 OperationLog.ACTION_CHOICES |
| `target_type` | VARCHAR(32) | 否 | — | user / resource / category |
| `target_id` | BIGINT | 是 | NULL | 操作对象 ID（批量操作时为 NULL） |
| `target_repr` | VARCHAR(256) | 否 | '' | 对象摘要（`str(instance)` 或人工写） |
| `after` | JSON | 是 | NULL | 操作后快照（dict，已序列化） |
| `ip_address` | VARCHAR(64) | 是 | NULL | 客户端 IP |
| `user_agent` | VARCHAR(512) | 否 | '' | User-Agent |
| `created_at` | DATETIME(6) | 否 | — | 操作时间 |

## 索引说明

| 索引 | 字段 | 用途 |
|------|------|------|
| PRIMARY | id | 主键 |
| idx_oplog_operator_created | (operator_id, created_at) | 按操作人查近期操作 |
| idx_oplog_target | (target_type, target_id) | 查某个具体对象的操作历史 |
| idx_oplog_action_created | (action, created_at) | 按动作类型筛 |
| idx_oplog_created | (created_at) | 全局时间范围 |

## 应用层

- Django 侧：`apps/admin_panel/models.py::OperationLog`，**`managed=False`**（由远程手动建表）
- 写入：`apps/admin_panel/utils.py::write_operation_log` 工具函数，**失败仅记日志不抛异常**
- 触发点（当前覆盖范围）：
  - `apps/admin_panel/views.py::UserAdminViewSet`
    - `create()` / `update()` / `partial_update()` / `destroy()` / `reset_password()` action
    - `BatchDeleteUsersView.post()`（action=batch_delete）
    - `BatchResetPasswordView.post()`（action=batch_reset_password）
- **未覆盖**：Resource / Category 当前没有 CRUD 入口，等后续添加

> ⚠️ **注意**：`managed=False`，Django migration 不参与。
> 误操作需走回滚 SQL。
