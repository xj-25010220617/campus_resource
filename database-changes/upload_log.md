---
## 变更 #2：新增 department_id 字段

> **变更类型**：字段新增
> **变更日期**：2026-07-03 11:24:00
> **变更人员**：zyr
> **变更原因**：上传日志表与 `resource` 表保持一致，新增 `department_id` 字段以支持部门管理员查看本部门上传记录，配合 `apps/admin_panel/permissions.py` 的 `scope_upload_logs` 范围过滤。
> **影响范围**：新增字段默认为 NULL，对现有数据零影响。
> **回滚方案**：`ALTER TABLE \`upload_log\` DROP FOREIGN KEY \`fk_ulog_department\`; ALTER TABLE \`upload_log\` DROP INDEX \`idx_ulog_department_id\`; ALTER TABLE \`upload_log\` DROP COLUMN \`department_id\`;`

```sql
ALTER TABLE `upload_log`
    ADD COLUMN `department_id` BIGINT NULL COMMENT '所属部门ID（与 college_id 互斥）' AFTER `college_id`;

ALTER TABLE `upload_log`
    ADD CONSTRAINT `fk_ulog_department`
        FOREIGN KEY (`department_id`) REFERENCES `department`(`id`) ON DELETE SET NULL;

CREATE INDEX `idx_ulog_department_id` ON `upload_log`(`department_id`);
```

**字段说明：**

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `department_id` | BIGINT | NULL, FK(department.id), ON DELETE SET NULL | 所属部门（与 college_id 互斥） |

**配套索引更新：**

| 索引名 | 字段 | 用途 |
|--------|------|------|
| `idx_ulog_department_id` | (department_id) | 部门管理员按部门过滤上传日志 |
| 原 `idx_ulog_college_created` | (college_id, created_at) | 学院管理员按学院过滤（保留） |

**应用层过滤：** `UploadLogViewSet.get_queryset()` 调用 `scope_upload_logs()`，按当前用户角色做范围过滤：超管全量、学院管理员本学院且 `department_id IS NULL`、部门管理员本部门且 `college_id IS NULL`。