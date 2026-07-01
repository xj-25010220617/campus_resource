# doc_chunk 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-07-01 00:00:00
> **变更人员**：Fressia-zzt
> **变更原因**：文档解析分块后，MySQL 存文本引用，Milvus 存向量，通过 vector_id 关联
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS doc_chunk;

```sql
CREATE TABLE `doc_chunk` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `doc_id` BIGINT NOT NULL,
    `chunk_index` INT NOT NULL,
    `content` LONGTEXT NOT NULL,
    `token_count` INT NOT NULL DEFAULT 0,
    `vector_id` VARCHAR(128) NOT NULL,
    `page_start` INT NULL,
    `page_end` INT NULL,
    `created_at` DATETIME(6) NOT NULL,
    CONSTRAINT `fk_chunk_doc` FOREIGN KEY (`doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE UNIQUE INDEX `uk_chunk_doc_index` ON `doc_chunk`(`doc_id`, `chunk_index`);
CREATE INDEX `idx_chunk_doc` ON `doc_chunk`(`doc_id`);
CREATE INDEX `idx_chunk_vector` ON `doc_chunk`(`vector_id`);
```
