# query_log 表

> **变更类型**：表创建（首次）  
> **变更日期**：2026-07-01  
> **变更人员**：3471691973-eg  
> **变更原因**：RAG 三层检索改造 — 记录用户提问与命中情况，用于检索质量分析与优化  
> **关联文档**：[RAG查询改造方案.md](../RAG查询改造方案.md)  
> **影响范围**：新建表，无现有数据影响  
> **回滚方案**：`DROP TABLE IF EXISTS query_log;`

```sql
CREATE TABLE `query_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `user_id` BIGINT NOT NULL,
    `conversation_id` BIGINT NULL,
    `question` LONGTEXT NOT NULL,
    `parsed_filters` JSON NULL,
    `hit_count` INTEGER NOT NULL DEFAULT 0,
    `top_doc_ids` JSON NULL,
    `response_ms` INTEGER NOT NULL DEFAULT 0,
    `user_feedback` TINYINT NOT NULL DEFAULT -1,
    `created_at` DATETIME(6) NOT NULL,

    CONSTRAINT `fk_qlog_user`
        FOREIGN KEY (`user_id`) REFERENCES `user`(`id`)
        ON DELETE CASCADE,

    CONSTRAINT `fk_qlog_conv`
        FOREIGN KEY (`conversation_id`) REFERENCES `chat_conversation`(`id`)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_qlog_user_created` ON `query_log`(`user_id`, `created_at`);
CREATE INDEX `idx_qlog_created` ON `query_log`(`created_at`);
CREATE INDEX `idx_qlog_feedback` ON `query_log`(`user_feedback`, `created_at`);
```

## 字段说明

| 字段 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | — | 主键 |
| `user_id` | `BIGINT` | 否 | — | 提问用户 ID，外键 → `user.id`，删除用户时级联 |
| `conversation_id` | `BIGINT` | 是 | `NULL` | 关联会话 ID，外键 → `chat_conversation.id`，会话删除时置 NULL |
| `question` | `LONGTEXT` | 否 | — | 用户原始问题 |
| `parsed_filters` | `JSON` | 是 | `NULL` | LLM 解析出的硬过滤条件，例：`{"college_id": 1, "file_type": "pdf"}` |
| `hit_count` | `INTEGER` | 否 | `0` | 召回命中数量 |
| `top_doc_ids` | `JSON` | 是 | `NULL` | 召回 Top-K 的 doc_id 列表，例：`[12, 45, 89]` |
| `response_ms` | `INTEGER` | 否 | `0` | 响应耗时（毫秒，SSE 流完成后回填） |
| `user_feedback` | `TINYINT` | 否 | `-1` | 用户反馈：`1`=赞 / `0`=踩 / `-1`=无反馈 |
| `created_at` | `DATETIME(6)` | 否 | — | 提问时间（自动） |

## 索引说明

| 索引 | 字段 | 用途 |
|------|------|------|
| `PRIMARY` | `id` | 主键 |
| `fk_qlog_user` | `user_id` | 外键约束 + 自动索引 |
| `fk_qlog_conv` | `conversation_id` | 外键约束 + 自动索引 |
| `idx_qlog_user_created` | `user_id`, `created_at` | 查某用户近期问题 |
| `idx_qlog_created` | `created_at` | 全局时间范围查询 |
| `idx_qlog_feedback` | `user_feedback`, `created_at` | 查点赞/点踩率高的问题 |

## 数据示例

```json
{
    "id": 1,
    "user_id": 5,
    "conversation_id": 12,
    "question": "计算机系大三下学期的操作系统复习资料",
    "parsed_filters": {
        "college_id": 1,
        "discipline_id": 3,
        "target_audience": "undergrad",
        "course_term": "操作系统",
        "category_ids": [10, 11, 12]
    },
    "hit_count": 5,
    "top_doc_ids": [1023, 1045, 1089],
    "response_ms": 1850,
    "user_feedback": 1,
    "created_at": "2026-07-01 14:32:18.452345"
}
```
