# moderation_attempt 表

> **变更类型**：表创建（首次）
> **变更日期**：2026-09-10 12:30:00
> **变更人员**：3471691973-eg
> **变更原因**：P1-2A——保存每一次审核尝试的**不可变历史**。
> `content_moderation` 只保存当前生效状态，会被后续尝试覆盖；本表回答"第几次审核、
> 什么时候、哪个 worker、什么策略、哪些检测器、什么结论、为什么失败"。
> **影响范围**：**新建表，不影响任何现有数据与现有查询**。本阶段无写入方，上线后为空表。
> **回滚方案**：`DROP TABLE IF EXISTS moderation_attempt;`

```sql
CREATE TABLE `moderation_attempt` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `moderation_id` BIGINT NOT NULL,
    `attempt_seq` INT UNSIGNED NOT NULL,
    `state_at_start` VARCHAR(24) NOT NULL DEFAULT 'pending',
    `started_at` DATETIME(6) NULL,
    `finished_at` DATETIME(6) NULL,
    `detector_results` JSON NOT NULL,
    `verdict` VARCHAR(24) NOT NULL DEFAULT '',
    `error` LONGTEXT NOT NULL,
    `policy_version` VARCHAR(64) NOT NULL DEFAULT '',
    `worker_id` VARCHAR(128) NOT NULL DEFAULT '',
    `created_at` DATETIME(6) NOT NULL,
    CONSTRAINT `uk_attempt_moderation_seq` UNIQUE (`moderation_id`, `attempt_seq`),
    CONSTRAINT `fk_attempt_moderation` FOREIGN KEY (`moderation_id`)
        REFERENCES `content_moderation`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `moderation_attempt_created_at_e42e8dc4` ON `moderation_attempt`(`created_at`);
CREATE INDEX `moderation_attempt_moderation_id_06fa9696` ON `moderation_attempt`(`moderation_id`);
```

---

## 字段说明

| 字段 | 说明 |
|------|------|
| `moderation_id` | 关联 `content_moderation`；级联删除 |
| `attempt_seq` | 尝试序号，从 1 递增。与 `moderation_id` 组成**唯一约束** |
| `state_at_start` | 本次尝试开始时的审核状态 |
| `started_at` / `finished_at` | 尝试起止时间 |
| `detector_results` | 各检测器原始结论（JSON）。**须先脱敏再落库**，不得写入未脱敏的命中原文 |
| `verdict` | 本次结论。空串表示本次未得出结论 |
| `error` | 异常信息 |
| `policy_version` | 策略版本，用于复现"同一份内容在不同策略下结论不同" |
| `worker_id` | 执行该次尝试的 worker 标识 |

## 为什么必须保存历史

- **申诉与取证**：需要知道当时是谁、按什么策略、得出什么结论。
- **调试聚合逻辑**：需要看到每次尝试的原始 finding。
- **策略演进**：`policy_version` 变化后要能区分"旧策略判 clean、新策略判 sensitive"。

## 唯一约束的作用

`uk_attempt_moderation_seq (moderation_id, attempt_seq)` 是本表的核心约束：

1. **幂等**：同一次尝试重复执行时，重复插入会被数据库拒绝。
2. **防旧覆新**：配合 `content_moderation.applied_seq` 做 CAS：

   ```sql
   UPDATE content_moderation
      SET state = ?, verdict = ?, applied_seq = ?
    WHERE id = ? AND applied_seq < ?
   ```

   影响行数为 0 即表示"已有更新的结果生效"，本次结果应被丢弃。
   **不要**用 `updated_at` 判断新旧——同秒精度与时钟漂移都会判错。

## 兼容性说明

- 纯新增表，不修改任何既有表结构；**旧代码在 migration 前后都能正常运行**。
- 本表不参与任何现有查询路径，上线不改变任何现有接口行为。
- migration **不含数据迁移**，不会为历史文档创建任何记录。

## 回滚说明

```sql
DROP TABLE IF EXISTS moderation_attempt;
```

- 本表依赖 `content_moderation`，回滚顺序为：先 `moderation_attempt`，再 `content_moderation`。
- 回滚**不影响 `knowledge_doc`** 及其数据。
- ⚠️ 与 `content_moderation` 同理：生产 CI 自动 `migrate`，回滚必须"先回代码、再回 migration"。
