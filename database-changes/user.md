---
## 变更 #2：role 字段 choices 扩展

> **变更类型**：字段修改（choices 扩展）
> **变更日期**：2026-07-03 11:24:00
> **变更人员**：zyr
> **变更原因**：将原三级角色 (`super_admin` / `admin` / `user`) 扩展为五级，新增 `college_admin`（学院管理员）和 `dept_admin`（部门管理员），实现"学院资源 / 部门资源"互斥归属的范围管理。`admin` 保留为兼容旧角色值。
> **影响范围**：仅字段 options 变化，不影响默认值 `user`，不影响已存在数据，无需数据迁移。
> **回滚方案**：无破坏性变更，回滚 migration 即可恢复。

```sql
-- 本次仅 choices 变更，无 DDL
ALTER TABLE `user` MODIFY COLUMN `role` VARCHAR(16) NOT NULL DEFAULT 'user'
    COMMENT '角色: super_admin=超管, college_admin=学院管理员, dept_admin=部门管理员, admin=兼容旧管理员, user=学生';

-- 可选：补充枚举值校验（MySQL 无原生 enum，可由应用层 validate 拦截）
```

**新增的角色枚举值：**

| 值 | 含义 | 归属约束 |
|----|------|----------|
| `super_admin` | 超级管理员 | 不限 |
| `college_admin` | 学院管理员 | 必须设置 `college_id`，`department_id` 必须为空 |
| `dept_admin` | 部门管理员 | 必须设置 `department_id`，`college_id` 必须为空 |
| `admin` | 管理员（兼容旧角色） | 行为等同于 `college_admin` |
| `user` | 普通用户/学生 | 必须设置 `college_id` |

**应用层校验：** `apps/accounts/models.py` 的 `User.clean()` 方法对角色与 `college_id` / `department_id` 的组合做强制校验，由 DRF 序列化器 `validate()` 方法同步复用。