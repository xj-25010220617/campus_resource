# content_moderation 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-09-10 12:30:00
> **变更人员**：3471691973-eg
> **变更原因**：P1-2A——为后续内容安全审核体系建立"当前生效审核状态"的存储。
> 采用与 `knowledge_doc` 一对一的独立表，避免把审核字段塞进 `knowledge_doc`
> （该表已有 `is_sensitive` / `sensitive_checked` 两个语义不清的死字段）。
> **影响范围**：**新建表，不影响任何现有数据与现有查询**。本阶段没有上传链路写入此表，
> 因此上线后该表在业务上为空，现有功能行为完全不变。
> **回滚方案**：`DROP TABLE IF EXISTS content_moderation;`
> （回滚前必须确认没有代码依赖该表；若新代码已上线，需先回滚代码再回滚 migration，见"兼容性说明"）

```sql
CREATE TABLE `content_moderation` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `doc_id` BIGINT NOT NULL,
    `state` VARCHAR(24) NOT NULL DEFAULT 'pending',
    `verdict` VARCHAR(24) NOT NULL DEFAULT '',
    `categories` JSON NOT NULL,
    `findings` JSON NOT NULL,
    `checked_at` DATETIME(6) NULL,
    `failure_reason` LONGTEXT NOT NULL,
    `service` VARCHAR(32) NOT NULL DEFAULT '',
    `policy_version` VARCHAR(64) NOT NULL DEFAULT '',
    `applied_seq` INT UNSIGNED NOT NULL DEFAULT 0,
    `storage_state` VARCHAR(20) NOT NULL DEFAULT 'quarantine',
    `quarantine_key` VARCHAR(512) NOT NULL DEFAULT '',
    `promoted_key` VARCHAR(512) NOT NULL DEFAULT '',
    `promotion_error` LONGTEXT NOT NULL,
    `promotion_retry` INT UNSIGNED NOT NULL DEFAULT 0,
    `lease_owner` VARCHAR(128) NOT NULL DEFAULT '',
    `lease_expires_at` DATETIME(6) NULL,
    `attempt_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `next_retry_at` DATETIME(6) NULL,
    `confirmed_by_id` BIGINT NULL,
    `confirmed_at` DATETIME(6) NULL,
    `confirmation_reason` LONGTEXT NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    CONSTRAINT `uk_cm_doc` UNIQUE (`doc_id`),
    CONSTRAINT `fk_cm_doc` FOREIGN KEY (`doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_cm_confirmed_by` FOREIGN KEY (`confirmed_by_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `content_moderation_state_a1d67bca` ON `content_moderation`(`state`);
CREATE INDEX `content_moderation_verdict_0d36b9cb` ON `content_moderation`(`verdict`);
CREATE INDEX `content_moderation_storage_state_a2c3182b` ON `content_moderation`(`storage_state`);
CREATE INDEX `content_moderation_next_retry_at_35288379` ON `content_moderation`(`next_retry_at`);
CREATE INDEX `content_moderation_created_at_fe772cb5` ON `content_moderation`(`created_at`);
CREATE INDEX `idx_cm_state_storage` ON `content_moderation`(`state`, `storage_state`);
```

---

## 字段说明（关键字段）

| 字段 | 说明 |
|------|------|
| `doc_id` | 一对一关联 `knowledge_doc`。**唯一约束**保证一份文档只有一条当前审核状态 |
| `state` | 审核流程状态：`pending` / `scanning` / `approved` / `rejected` / `unavailable` / `confirmed_unchecked` / `error` |
| `verdict` | 内容性质结论：`clean` / `virus` / `sensitive` / `harmful` / `negative_guidance` / `mixed` / `unavailable` / `error`。**空串表示尚未产生结论**，不属于任何一类 |
| `categories` | 结论类别列表（JSON），供列表筛选与统计 |
| `findings` | 审核证据（JSON）。结构约束见 `apps/knowledge/moderation.py`：最多 50 条、单条 `excerpt` ≤ 40 字符、序列化总字节 ≤ 64KB |
| `applied_seq` | 已生效的尝试序号。未来用 CAS（`WHERE applied_seq < ?`）拦截"旧任务覆盖新结果"，**不要**用时间戳判新旧 |
| `storage_state` | 对象所在命名空间：`quarantine` / `promoting` / `promoted` / `promotion_failed` / `deleted` |
| `quarantine_key` | 隔离区对象 Key（`quarantine/…`） |
| `promoted_key` | 正式区对象 Key（`knowledge/…`），promotion 成功后写入 |
| `lease_owner` / `lease_expires_at` | 审核任务租约，用于僵死 worker 恢复 |
| `attempt_count` / `next_retry_at` | 有界重试控制 |
| `confirmed_by_id` / `confirmed_at` / `confirmation_reason` | 人工确认放行三要素，见下方安全不变量 |

## 与 `knowledge_doc.oss_key` 的关系

- `knowledge_doc.oss_key`：**现有语义不变**，本阶段未改动、未迁移。
- 审核是否通过**只看本表的 `state`**。`oss_key` 存在、文档 `status` 为 `uploaded`/`indexed`、
  文件能正常解析——**都不等价于审核通过**。

## 安全不变量（表级）

1. **放行白名单**：仅 `state IN ('approved', 'confirmed_unchecked')` 允许放行。
   判断必须用白名单，禁止写成 `state != 'rejected'`——黑名单在新增状态时会静默放行。
2. **`unavailable` ≠ `approved`**：检测链未能完成时停在 `unavailable`，绝不因为"扫描失败"当干净放行。
3. **`confirmed_unchecked` 不能自动产生**：只允许由 `unavailable` + 显式人工动作产生，
   且 `confirmed_by_id`、`confirmed_at`、非空 `confirmation_reason` 三者必须同时具备。
   禁止任何定时任务或超时逻辑写入该状态。

## 兼容性说明

- 本表为**纯新增表**，不修改 `knowledge_doc` 任何字段，因此**旧代码在 migration 前后都能正常运行**。
- 本表**不参与**任何现有查询路径（列表 / 详情 / 下载 / 预览 / RAG / FAQ / 爬虫），
  因此上线不改变任何现有接口行为。
- migration **不含数据迁移**：不会为历史文档创建记录，不会写入 `approved` / `clean`。
  历史数据策略（`legacy_unchecked` 打底 + 分批重扫）留给后续阶段。

## 回滚说明

```sql
DROP TABLE IF EXISTS content_moderation;
```

- 该表无外部依赖（除 `moderation_attempt` 的外键，需先回滚 `moderation_attempt`），
  回滚**不影响 `knowledge_doc`** 及其数据。
- ⚠️ 生产 CI 在 push 到 `main` 时会自动执行 `migrate`：**回滚不是简单地把 migration 退回去**。
  如果新代码已经依赖本表（P1-2D 之后的上传流水线），必须先回滚代码、再回滚 migration，
  否则新代码会因表不存在而报错。
