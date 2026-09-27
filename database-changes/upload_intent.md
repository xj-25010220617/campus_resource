# upload_intent 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-09-10 14:55:00
> **变更人员**：3471691973-eg
> **变更原因**：P1-2D——为上传链路建立**服务端签发**的上传凭证记录。
> Phase 1-1 报告中的 `callback ownership = BLOCKED` 项，根因是 STS 直传路径
> 服务端从未持有"我签发过这个 key"的可信记录：回调阶段只能校验客户端回传
> `oss_key` 的格式与 scope 前缀，无法证明该对象由本次请求的发起者上传，
> 同一 scope 目录下可以直接挂载他人的对象。
> 本表把 `(user, quarantine_key)` 的绑定关系在**上传初始化阶段**写死，
> 回调阶段只认 intent id，key 与归属一律以库里的记录为准。
> **影响范围**：**新建表，不影响任何现有数据与现有查询**。本阶段（STEP 3）
> 不接入任何上传入口，因此上线后该表在业务上为空，现有上传/索引行为完全不变。
> **回滚方案**：见文末"回滚说明"。

```sql
CREATE TABLE `upload_intent` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `scope` VARCHAR(16) NOT NULL,
    `owner_id` INT UNSIGNED NOT NULL DEFAULT 0,
    `user_id` BIGINT NULL,
    `quarantine_key` VARCHAR(512) NOT NULL UNIQUE,
    `original_filename` VARCHAR(256) NOT NULL,
    `content_type` VARCHAR(128) NOT NULL DEFAULT '',
    `expected_size` BIGINT NULL,
    `actual_size` BIGINT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'issued',
    `expires_at` DATETIME(6) NOT NULL,
    `completed_at` DATETIME(6) NULL,
    `consumed_at` DATETIME(6) NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    CONSTRAINT `upload_intent_user_id_fk`
        FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
);
CREATE INDEX `upload_intent_status_idx` ON `upload_intent`(`status`);
CREATE INDEX `upload_intent_expires_at_idx` ON `upload_intent`(`expires_at`);
CREATE INDEX `upload_intent_created_at_idx` ON `upload_intent`(`created_at`);
CREATE INDEX `idx_ui_user_status` ON `upload_intent`(`user_id`, `status`);
CREATE INDEX `idx_ui_status_expires` ON `upload_intent`(`status`, `expires_at`);
```

---

## 字段说明

| 字段 | 说明 |
|------|------|
| `scope` / `owner_id` | 归属范围与归属对象 ID。取值与现有 4 个上传入口**逐字一致**（super_admin→`school`/0；dept_admin→`department`/department_id；其余→`college`/college_id）。复用 `knowledge_doc.SCOPE_CHOICES`，未新建第二套含义 |
| `user_id` | 上传凭证的归属用户。**权限校验以此为准**，不以 `scope` 为准（`scope` 只是目录归属，同 scope 下可能是别人） |
| `quarantine_key` | 隔离区对象 Key。**由服务端调用 `utils.oss.build_quarantine_key()` 生成**，必然是 `quarantine/` 前缀，**不得**是 `knowledge/`。**唯一约束**保证一个对象只对应一个 intent |
| `original_filename` | 原始文件名（已去路径分隔符，仅保留 basename），仅供展示 |
| `content_type` | 上传时声明的 MIME，可为空 |
| `expected_size` | 初始化阶段声明的文件大小（可选） |
| `actual_size` | 上传完成后服务端核实到的大小。与 `expected_size` 不一致时**拒绝完成** |
| `status` | 生命周期状态，见下节 |
| `expires_at` | 过期时间，由 `settings.UPLOAD_INTENT_TTL_SECONDS`（默认 3600 秒）决定 |
| `completed_at` | 对象已确认存在于隔离区的时间 |
| `consumed_at` | 该 intent 被业务消费的时间 |

## 生命周期

```
issued ──complete──> completed ──consume──> consumed   （正常路径，单向）
   │                     │
   └──────expire─────────┴──────> expired              （超过 expires_at）
   │
   └──────cancel────────────────> cancelled            （主动作废）
```

- `completed_at` 与 `consumed_at` **语义不重叠**，故两个字段都保留：
  `completed_at` = 对象已上传到隔离区（尚未建任何业务记录）；
  `consumed_at` = 业务已基于该对象建过记录，此 intent 不能再消费。
- `consumed` / `expired` / `cancelled` 均为**终态**，不能回到之前的任何状态。
- 状态**只能由服务端**在 `apps.knowledge.upload_intent` 内变更；客户端提交的
  任何状态字段一律忽略。

## 为什么不用 `is_sensitive` / `sensitive_checked`

那两列在 `knowledge_doc` 上语义不清且**在上传路径中从未被写入**，属于死字段。
本表只记录"上传凭证"这一事实，**不表达任何安全性结论**——
安全结论必须由 `content_moderation` 表达（P1-2C）。

## 唯一约束的作用

`quarantine_key UNIQUE` 保证同一个隔离区对象不可能对应两个 intent，
从而杜绝"一个对象被两次签发、两次消费、生成两个 KnowledgeDoc"。

## 级联与删除策略

- `user_id` → `user.id`：**SET NULL**。用户注销后保留凭证记录（对象仍在隔离区，
  属于待处理的安全证据）。`user_id IS NULL` 的 intent **任何人都不能消费**——
  fail-closed，没有归属人就没有合法消费方。
- `chunked_upload_session.intent_id` → `upload_intent.id`：**PROTECT**。
  仍被分片会话引用的 intent 不允许删除，避免留下指向已删意图的孤儿会话。

## 兼容性说明

- 纯新增表 + 既有表新增一个**可空**列（见 [chunked_upload_session.md](chunked_upload_session.md)），
  **旧代码在 migration 前后都能正常运行**。
- 本表不参与任何现有查询路径，上线不改变任何现有接口行为。
- migration **不含数据迁移**：不为历史上传补建记录，不写任何 `approved`，
  不扫描 OSS，不调用任何外部服务。

## 回滚说明

```sql
-- 1) 先删外键列（chunked_upload_session.intent_id）
ALTER TABLE `chunked_upload_session` DROP FOREIGN KEY `chunked_upload_session_intent_id_fk`;
ALTER TABLE `chunked_upload_session` DROP COLUMN `intent_id`;
-- 2) 再删本表
DROP TABLE IF EXISTS `upload_intent`;
```

- 回滚**不影响 `knowledge_doc` / `chunked_upload_session` 的既有数据**。
- ⚠️ 生产 CI 自动 `migrate`，回滚必须"**先回代码、再回 migration**"。
