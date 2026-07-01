# feedback_question 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-07-01  
> **变更人员**：GJ-ml  
> **变更原因**：人工兜底模块 — AI 无法回答时问题自动进入待回答队列，admin 可提交人工回答并索引入库  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：DROP TABLE IF EXISTS feedback_question;

```sql
CREATE TABLE `feedback_question` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `question` LONGTEXT NOT NULL,
    `related_message_id` BIGINT NULL,
    `college_id` BIGINT NULL,
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending',
    `answer_text` LONGTEXT NOT NULL,
    `answered_by_id` BIGINT NULL,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    `answered_at` DATETIME(6) NULL,
    INDEX `idx_fq_status` (`status`),
    INDEX `idx_fq_college` (`college_id`),
    INDEX `idx_fq_status_college` (`status`, `college`),
    CONSTRAINT `fk_fq_college` FOREIGN KEY (`college_id`) REFERENCES `college`(`id`) ON DELETE SET NULL,
    CONSTRAINT `fk_fq_answerer` FOREIGN KEY (`answered_by_id`) REFERENCES `user`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | 主键 |
| `question` | LONGTEXT | 未能回答的问题原文 |
| `related_message_id` | BIGINT NULL | 关联的 AI 回复消息 ID（chat app 尚未创建，暂为软引用） |
| `college_id` | BIGINT FK→college | 应转发到哪个学院 |
| `status` | VARCHAR(16) | pending / answered / ignored |
| `answer_text` | LONGTEXT | 人工回答内容 |
| `answered_by_id` | BIGINT FK→user | 回答人 |
| `created_at` | DATETIME(6) | 创建时间 |
| `answered_at` | DATETIME(6) NULL | 回答时间 |

**备注：**
- `related_message_id` 暂不使用 FK 约束，待 `chat.Message` 模型创建后添加
- `status` 的 `ignored` 状态预留，MVP 阶段主要使用 `pending` → `answered` 流转
