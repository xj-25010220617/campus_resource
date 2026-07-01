# college 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-06-30 15:30:00  
> **变更人员**：xj-25010220617  
> **变更原因**：MVP 初始化，创建二级学院基础表  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS college;

```sql
CREATE TABLE `college` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `name` VARCHAR(64) NOT NULL UNIQUE,
    `code` VARCHAR(16) NOT NULL UNIQUE,
    `sort_order` INTEGER NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```
