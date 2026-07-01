# upload_log 表设计

> **变更日期**：2026-07-01
> **变更类型**：新增表
> **用途**：记录资源上传操作日志

---

## 1. 表结构

```sql
CREATE TABLE `upload_log` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
    `user_id` BIGINT NOT NULL COMMENT '上传用户ID',
    `resource_id` BIGINT NULL COMMENT '关联资源ID（上传成功后）',
    `file_name` VARCHAR(256) NOT NULL COMMENT '原始文件名',
    `file_size` BIGINT NOT NULL COMMENT '文件大小（字节）',
    `file_type` VARCHAR(16) NOT NULL COMMENT '文件类型（pdf/doc/ppt/xls/image/video/audio/other）',
    `college_id` BIGINT NOT NULL COMMENT '所属学院ID',
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '状态：pending/success/failed',
    `error_message` TEXT NULL COMMENT '失败时的错误信息',
    `ip_address` VARCHAR(45) NULL COMMENT '客户端IP地址（支持IPv6）',
    `user_agent` VARCHAR(512) NULL COMMENT '浏览器User-Agent',
    `created_at` DATETIME(6) NOT NULL COMMENT '创建时间',
    `completed_at` DATETIME(6) NULL COMMENT '完成时间',
    
    -- 外键
    CONSTRAINT `fk_ulog_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_ulog_resource` FOREIGN KEY (`resource_id`) REFERENCES `resource`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_ulog_college` FOREIGN KEY (`college_id`) REFERENCES `college`(`id`) ON DELETE RESTRICT,
    
    -- 索引
    INDEX `idx_ulog_user_created` (`user_id`, `created_at`),
    INDEX `idx_ulog_status_created` (`status`, `created_at`),
    INDEX `idx_ulog_college_created` (`college_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='上传日志';
```

---

## 2. 字段说明

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BIGINT | PK, AUTO_INCREMENT | 自增主键 |
| `user_id` | BIGINT | NOT NULL, FK(user.id) | 上传用户 |
| `resource_id` | BIGINT | NULL, FK(resource.id) | 上传成功后关联资源 |
| `file_name` | VARCHAR(256) | NOT NULL | 原始文件名 |
| `file_size` | BIGINT | NOT NULL | 文件大小（字节） |
| `file_type` | VARCHAR(16) | NOT NULL | 文件类型枚举值 |
| `college_id` | BIGINT | NOT NULL, FK(college.id) | 所属学院 |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'pending' | 上传状态 |
| `error_message` | TEXT | NULL | 错误信息（失败时填写） |
| `ip_address` | VARCHAR(45) | NULL | 客户端IP（支持IPv6） |
| `user_agent` | VARCHAR(512) | NULL | 浏览器标识 |
| `created_at` | DATETIME(6) | NOT NULL | 创建时间 |
| `completed_at` | DATETIME(6) | NULL | 完成时间 |

---

## 3. 状态说明

| 状态值 | 说明 |
|--------|------|
| `pending` | 待处理（上传中） |
| `success` | 成功（资源已创建） |
| `failed` | 失败（记录错误原因） |

---

## 4. 索引设计

| 索引名 | 字段 | 用途 |
|--------|------|------|
| `idx_ulog_user_created` | (user_id, created_at) | 按用户查询上传记录 |
| `idx_ulog_status_created` | (status, created_at) | 按状态统计/过滤 |
| `idx_ulog_college_created` | (college_id, created_at) | 按学院统计 |

---

## 5. API 接口

### 5.1 日志列表

| 项目 | 说明 |
|------|------|
| **接口** | `GET /api/resources/upload-log/` |
| **功能** | 上传日志列表（分页/过滤） |
| **权限** | `admin` 或 `super_admin` |
| **过滤参数** | `status`, `file_type`, `college`, `user_id`, `start_date`, `end_date` |
| **返回** | 分页日志列表 |

### 5.2 日志详情

| 项目 | 说明 |
|------|------|
| **接口** | `GET /api/resources/upload-log/{id}/` |
| **功能** | 上传日志详情 |
| **权限** | `admin` 或 `super_admin` |
| **返回** | 日志详情对象 |

### 5.3 上传统计

| 项目 | 说明 |
|------|------|
| **接口** | `GET /api/resources/upload-log/stats/` |
| **功能** | 上传统计（总数/成功数/失败数/按学院/按用户） |
| **权限** | `admin` 或 `super_admin` |
| **返回** | 统计数据对象 |

---

## 6. 迁移文件

- `apps/resources/migrations/0004_upload_log.py`

---

## 7. SQL 执行

```sql
-- 执行迁移（如果使用 Django migrate）
cd backend
python manage.py migrate resources

-- 或手动执行 DDL
CREATE TABLE `upload_log` (...);
```
