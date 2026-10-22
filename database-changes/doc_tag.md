# doc_tag 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-10-22 22:01:41
> **变更人员**：userName
> **变更原因**：文档-标签多对多关联表，记录某文档被打了哪些标签及打标人；与 keyword 分离，标签不带权重
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS doc_tag;

```sql
CREATE TABLE `doc_tag` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `doc_id` BIGINT NOT NULL,
    `tag_id` BIGINT NOT NULL,
    `created_by_id` BIGINT NULL,
    `created_at` DATETIME(6) NOT NULL,
    CONSTRAINT `uk_doc_tag` UNIQUE (`doc_id`, `tag_id`),
    CONSTRAINT `fk_dt_doc` FOREIGN KEY (`doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dt_tag` FOREIGN KEY (`tag_id`) REFERENCES `tag`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dt_creator` FOREIGN KEY (`created_by_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_dt_doc` ON `doc_tag`(`doc_id`);
CREATE INDEX `idx_dt_tag` ON `doc_tag`(`tag_id`);
```
