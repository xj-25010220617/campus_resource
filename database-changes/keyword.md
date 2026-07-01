# keyword 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-07-01 00:00:00
> **变更人员**：Fressia-zzt
> **变更原因**：存储上传员为文档手动添加的关键词，解决"文件内容无法提取文字"时的检索问题
> **影响范围**：新建表，不影响现有数据
> **回滚方案**：DROP TABLE IF EXISTS keyword;

```sql
CREATE TABLE `keyword` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `doc_id` BIGINT NOT NULL,
    `phrase` VARCHAR(256) NOT NULL,
    `match_type` VARCHAR(16) NOT NULL DEFAULT 'fuzzy',
    `weight` INT NOT NULL DEFAULT 1,
    `created_by_id` BIGINT NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    `updated_at` DATETIME(6) NOT NULL,
    CONSTRAINT `fk_kw_doc` FOREIGN KEY (`doc_id`) REFERENCES `knowledge_doc`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_kw_creator` FOREIGN KEY (`created_by_id`) REFERENCES `user`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_kw_doc` ON `keyword`(`doc_id`);
CREATE INDEX `idx_kw_phrase` ON `keyword`(`phrase`);
CREATE FULLTEXT INDEX `ft_kw_phrase` ON `keyword`(`phrase`) WITH PARSER ngram;
```
