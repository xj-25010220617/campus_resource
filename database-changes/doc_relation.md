# doc_relation 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-10-22 22:01:41
> **变更人员**：userName
> **变更原因**：文档间有向关联（文件网）——源文档指向目标文档并带关联类型；`is_deleted`/`deleted_at` 表示撤边（软删），保留历史
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS doc_relation;

```sql
CREATE TABLE `doc_relation` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `source_doc_id` BIGINT NOT NULL,
    `target_doc_id` BIGINT NOT NULL,
    `relation_type` VARCHAR(16) NOT NULL DEFAULT 'supplement',
    `created_by_id` BIGINT NULL,
    `is_deleted` TINYINT(1) NOT NULL DEFAULT 0,
    `deleted_at` DATETIME(6) NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    CONSTRAINT `uk_doc_relation` UNIQUE (`source_doc_id`, `target_doc_id`, `relation_type`),
    CONSTRAINT `fk_rel_source` FOREIGN KEY (`source_doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_rel_target` FOREIGN KEY (`target_doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_rel_creator` FOREIGN KEY (`created_by_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_rel_source` ON `doc_relation`(`source_doc_id`);
CREATE INDEX `idx_rel_target` ON `doc_relation`(`target_doc_id`);
```
