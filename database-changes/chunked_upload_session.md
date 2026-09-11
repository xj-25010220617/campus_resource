# chunked_upload_session 表

> **变更类型**：新增字段（`intent_id`）
> **变更日期**：2026-09-10 14:55:00
> **变更人员**：3471691973-eg
> **变更原因**：P1-2D——把分片上传会话关联到服务端签发的**上传意图**
> （`upload_intent`，见 [upload_intent.md](upload_intent.md)），
> 使分片路径与直传路径共用同一套上传凭证与归属校验。
> **影响范围**：**纯新增可空列 + 可空唯一外键**。既有行的 `intent_id` 全为 `NULL`，
> 现有分片上传/合并/索引行为**完全不变**（本阶段只建立关系，不切换隔离区路径）。
> **回滚方案**：见文末。

## 表结构（本表既有定义，此处补录）

本表由 `0006_chunkeduploadsession_and_more` 创建，此前没有独立的变更记录，
本次一并补录。

```sql
CREATE TABLE `chunked_upload_session` (
    `upload_id` VARCHAR(128) NOT NULL PRIMARY KEY,
    `user_id` BIGINT NOT NULL,
    `file_name` VARCHAR(256) NOT NULL,
    `file_size` BIGINT NOT NULL DEFAULT 0,
    `file_md5` VARCHAR(32) NOT NULL,
    `oss_key` VARCHAR(512) NOT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'uploading',
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    CONSTRAINT `chunked_upload_session_user_id_fk`
        FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
);
CREATE INDEX `idx_cus_user_status` ON `chunked_upload_session`(`user_id`, `status`);
CREATE INDEX `idx_cus_md5_status` ON `chunked_upload_session`(`file_md5`, `status`);
```

## 本次变更

```sql
ALTER TABLE `chunked_upload_session`
    ADD COLUMN `intent_id` BIGINT NULL,
    ADD CONSTRAINT `chunked_upload_session_intent_id_fk`
        FOREIGN KEY (`intent_id`) REFERENCES `upload_intent`(`id`) ON DELETE RESTRICT;
CREATE UNIQUE INDEX `chunked_upload_session_intent_id_key`
    ON `chunked_upload_session`(`intent_id`);
```

---

## 字段说明

| 字段 | 说明 |
|------|------|
| `intent_id` | 关联的上传意图。**可空**：历史会话没有 intent，尚未接入的上传入口创建的会话也不会带 intent |
| `upload_id` | OSS Multipart Upload ID，兼作本表主键 |
| `oss_key` | 本阶段**仍指向 `knowledge/`**。切换到隔离区（`quarantine/` + 审核 + promotion）是后续 STEP |

## 为什么是「可空唯一」而不是「非空唯一」

Django 的 `OneToOneField(null=True)` 在 MySQL / SQLite / GaussDB 上都允许存在
**多个 `NULL`**，因此：

- 存量会话行（`intent_id IS NULL`）不受影响，可无限多；
- 已绑定的 intent 严格一对一，重复绑定会被唯一索引拒绝。

避免使用 `UniqueConstraint(condition=...)` 这类**部分索引**——
MySQL 不支持部分索引，Django 只会给出 `models.W036` 警告并**静默不建约束**，
对一条安全相关的约束来说，静默失效是不可接受的。

## 为什么是 PROTECT（`ON DELETE RESTRICT`）

分片会话是上传行为的审计证据。若允许 intent 被删除而会话留存，就会出现
"指向已删意图的孤儿会话"；若用 `CASCADE`，删 intent 会连带删掉会话，
等于**通过删除凭证来抹掉上传痕迹**。因此选择 RESTRICT：仍被会话引用的 intent
不允许删除，宁可拒绝删除也不静默置空。

（注意方向：本约束保护的是 intent 不被误删，**不影响**删除会话本身。）

## 兼容性说明

- 可空列 + 可空唯一索引，**旧代码在 migration 前后都能正常运行**。
- 本阶段**不修改** `chunked_views.py`：`ChunkUploadInitView` / `ChunkUploadView` /
  `ChunkMergeView` 的行为、`oss_key` 的生成方式、merge 后触发索引的时机均保持不变。
- migration **不含数据迁移**：不为历史会话回填 intent，不扫描 OSS。

## 回滚说明

```sql
ALTER TABLE `chunked_upload_session` DROP FOREIGN KEY `chunked_upload_session_intent_id_fk`;
DROP INDEX `chunked_upload_session_intent_id_key` ON `chunked_upload_session`;
ALTER TABLE `chunked_upload_session` DROP COLUMN `intent_id`;
```

- 回滚只影响本次新增的列，**既有会话数据不受影响**。
- 回滚顺序：先回 `chunked_upload_session` 的列，再删 `upload_intent` 表。
- ⚠️ 生产 CI 自动 `migrate`，回滚必须"**先回代码、再回 migration**"。
