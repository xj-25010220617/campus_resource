# user 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-06-30 15:30:00  
> **变更人员**：xj-25010220617  
> **变更原因**：MVP 初始化，自定义用户模型，扩展 Django AbstractUser  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS user;

```sql
CREATE TABLE `user` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `password` VARCHAR(128) NOT NULL,
    `last_login` DATETIME(6) NULL,
    `is_superuser` BOOL NOT NULL DEFAULT 0,
    `username` VARCHAR(150) NOT NULL UNIQUE,
    `first_name` VARCHAR(150) NOT NULL,
    `last_name` VARCHAR(150) NOT NULL,
    `email` VARCHAR(254) NOT NULL,
    `is_staff` BOOL NOT NULL DEFAULT 0,
    `is_active` BOOL NOT NULL DEFAULT 1,
    `date_joined` DATETIME(6) NOT NULL,
    `role` VARCHAR(16) NOT NULL DEFAULT 'teacher',
    `phone` VARCHAR(11) NOT NULL DEFAULT '',
    `avatar` VARCHAR(200) NOT NULL DEFAULT '',
    `college_id` BIGINT NULL,
    CONSTRAINT `fk_user_college` FOREIGN KEY (`college_id`) REFERENCES `college`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_user_college` ON `user`(`college_id`);
```
