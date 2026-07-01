# resource 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-06-30 15:30:00  
> **变更人员**：xj-25010220617  
> **变更原因**：MVP 初始化，创建资源核心表  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS resource;

```sql
CREATE TABLE `resource` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `title` VARCHAR(256) NOT NULL,
    `description` LONGTEXT NOT NULL DEFAULT '',
    `oss_key` VARCHAR(512) NOT NULL,
    `oss_url` VARCHAR(1024) NOT NULL,
    `file_name` VARCHAR(256) NOT NULL,
    `file_size` BIGINT NOT NULL,
    `file_type` VARCHAR(16) NOT NULL,
    `md5_hash` VARCHAR(32) NOT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'draft',
    `audit_comment` LONGTEXT NOT NULL DEFAULT '',
    `view_count` INTEGER NOT NULL DEFAULT 0,
    `download_count` INTEGER NOT NULL DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    `published_at` DATETIME(6) NULL,
    `discipline_id` BIGINT NOT NULL,
    `category_id` BIGINT NOT NULL,
    `uploader_id` BIGINT NOT NULL,
    `college_id` BIGINT NOT NULL,
    `auditor_id` BIGINT NULL,
    CONSTRAINT `fk_resource_discipline` FOREIGN KEY (`discipline_id`) REFERENCES `discipline`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_resource_category` FOREIGN KEY (`category_id`) REFERENCES `category`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_resource_uploader` FOREIGN KEY (`uploader_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_resource_college` FOREIGN KEY (`college_id`) REFERENCES `college`(`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_resource_auditor` FOREIGN KEY (`auditor_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_res_disc_college` ON `resource`(`discipline_id`, `college_id`);
CREATE INDEX `idx_res_status_created` ON `resource`(`status`, `created_at`);
CREATE INDEX `idx_res_uploader_created` ON `resource`(`uploader_id`, `created_at`);
CREATE INDEX `idx_res_md5` ON `resource`(`md5_hash`);
```
