# favorite 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-06-30 15:30:00  
> **变更人员**：xj-25010220617  
> **变更原因**：MVP 初始化，创建资源收藏表  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS favorite;

```sql
CREATE TABLE `favorite` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `user_id` BIGINT NOT NULL,
    `resource_id` BIGINT NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    CONSTRAINT `fk_favorite_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_favorite_resource` FOREIGN KEY (`resource_id`) REFERENCES `resource`(`id`) ON DELETE CASCADE,
    UNIQUE KEY `uq_favorite_user_resource` (`user_id`, `resource_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```
