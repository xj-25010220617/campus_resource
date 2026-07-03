---
## 变更 #2：新增 department_id 字段

> **变更类型**：字段新增
> **变更日期**：2026-07-03 11:24:00
> **变更人员**：zyr
> **变更原因**：实现"学院资源 / 部门资源"互斥归属 — 资源要么归属学院，要么归属部门，二者只能选其一。新增可空外键 `department_id`，配合 `college_id` 字段一起表示归属。
> **影响范围**：新增字段默认为 NULL，对现有数据零影响。应用层（`apps/admin_panel/permissions.py` 的 `scope_resources`）按 `college_id IS NOT NULL XOR department_id IS NOT NULL` 做互斥约束。
> **回滚方案**：`ALTER TABLE \`resource\` DROP FOREIGN KEY \`fk_resource_department\`; ALTER TABLE \`resource\` DROP INDEX \`idx_resource_department_id\`; ALTER TABLE \`resource\` DROP COLUMN \`department_id\`;`

```sql
ALTER TABLE `resource`
    ADD COLUMN `department_id` BIGINT NULL COMMENT '所属部门ID（与 college_id 互斥：非空则 college_id 必须为空）' AFTER `college_id`;

ALTER TABLE `resource`
    ADD CONSTRAINT `fk_resource_department`
        FOREIGN KEY (`department_id`) REFERENCES `department`(`id`) ON DELETE SET NULL;

CREATE INDEX `idx_resource_department_id` ON `resource`(`department_id`);
```

**字段说明：**

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `department_id` | BIGINT | NULL, FK(department.id), ON DELETE SET NULL | 所属部门（与 college_id 互斥） |

**互斥语义：**
- 学院资源：`college_id` 非空 AND `department_id` IS NULL
- 部门资源：`department_id` 非空 AND `college_id` IS NULL
- 公共资源：两者均为 NULL（默认行为，便于跨部门文档）