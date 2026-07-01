# category 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-06-30 15:30:00  
> **变更人员**：xj-25010220617  
> **变更原因**：MVP 初始化，创建资源类型分类表（支持两级树形）  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS category;

```sql
CREATE TABLE `category` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `name` VARCHAR(64) NOT NULL,
    `parent_id` BIGINT NULL,
    `sort_order` INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT `fk_category_parent` FOREIGN KEY (`parent_id`) REFERENCES `category`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_category_parent` ON `category`(`parent_id`);
```
