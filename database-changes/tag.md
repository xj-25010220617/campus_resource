# tag 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-10-22 22:01:41
> **变更人员**：userName
> **变更原因**：标签字典表（全平台共享，无学院隔离）。与关键词（keyword）概念/操作分离——标签为人工打标、不带检索权重，用于浏览/筛选/聚合
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS tag;

```sql
CREATE TABLE `tag` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `name` VARCHAR(64) NOT NULL,
    `is_deleted` TINYINT(1) NOT NULL DEFAULT 0,
    `deleted_at` DATETIME(6) NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    UNIQUE KEY `uk_tag_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_tag_created` ON `tag`(`created_at`);
```
