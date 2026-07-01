# knowledge_doc 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-07-01 00:00:00
> **变更人员**：Fressia-zzt
> **变更原因**：知识库核心表，存储知识文档元数据、OSS 信息和索引状态
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS knowledge_doc;

```sql
CREATE TABLE `knowledge_doc` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `title` VARCHAR(512) NOT NULL,
    `description` LONGTEXT NOT NULL,
    `source_type` VARCHAR(16) NOT NULL,
    `source_url` VARCHAR(1024) NOT NULL DEFAULT '',
    `oss_key` VARCHAR(512) NOT NULL DEFAULT '',
    `oss_url` VARCHAR(1024) NOT NULL DEFAULT '',
    `file_name` VARCHAR(256) NOT NULL DEFAULT '',
    `file_type` VARCHAR(16) NOT NULL DEFAULT 'other',
    `file_size` BIGINT NOT NULL DEFAULT 0,
    `md5_hash` VARCHAR(32) NOT NULL DEFAULT '',
    `page_count` INT NOT NULL DEFAULT 0,
    `discipline_id` BIGINT NULL,
    `category_id` BIGINT NULL,
    `college_id` BIGINT NULL,
    `uploader_id` BIGINT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'uploaded',
    `chunk_count` INT NOT NULL DEFAULT 0,
    `is_sensitive` TINYINT(1) NOT NULL DEFAULT 0,
    `sensitive_checked` TINYINT(1) NOT NULL DEFAULT 0,
    `freshness_date` DATE NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    `indexed_at` DATETIME(6) NULL,
    CONSTRAINT `fk_kd_discipline` FOREIGN KEY (`discipline_id`) REFERENCES `discipline`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_kd_category` FOREIGN KEY (`category_id`) REFERENCES `category`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_kd_college` FOREIGN KEY (`college_id`) REFERENCES `college`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_kd_uploader` FOREIGN KEY (`uploader_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_kd_source_type` ON `knowledge_doc`(`source_type`);
CREATE INDEX `idx_kd_status` ON `knowledge_doc`(`status`);
CREATE INDEX `idx_kd_md5` ON `knowledge_doc`(`md5_hash`);
CREATE INDEX `idx_kd_college` ON `knowledge_doc`(`college_id`);
CREATE INDEX `idx_kd_discipline` ON `knowledge_doc`(`discipline_id`);
CREATE INDEX `idx_kd_category` ON `knowledge_doc`(`category_id`);
CREATE INDEX `idx_kd_uploader` ON `knowledge_doc`(`uploader_id`);
CREATE INDEX `idx_kd_created` ON `knowledge_doc`(`created_at`);
CREATE INDEX `idx_kd_freshness` ON `knowledge_doc`(`freshness_date`);
CREATE FULLTEXT INDEX `ft_kd_search` ON `knowledge_doc`(`title`, `description`) WITH PARSER ngram;
```
