# resource 表 — 字段追加

> **变更类型**：字段追加（已存在表的 ALTER）  
> **变更日期**：2026-07-01  
> **变更人员**：3471691973-eg  
> **变更原因**：RAG 三层检索改造 — 增加可见层级 / 适用对象 / 课程学期 / 关键词字段，供硬过滤与语义加权使用  
> **关联文档**：[RAG查询改造方案.md](../RAG查询改造方案.md)  
> **影响范围**：现有 `resource` 表记录中新增字段均为默认值（不会破坏已有数据）  
> **回滚方案**：见下方"回滚 SQL"

---

## 一、字段新增 SQL

```sql
-- 1. 可见层级
ALTER TABLE `resource`
    ADD COLUMN `visibility` VARCHAR(16) NOT NULL DEFAULT 'public'
        COMMENT '可见层级：public=公开 / internal=校内 / restricted=授权';

-- 2. 适用对象
ALTER TABLE `resource`
    ADD COLUMN `target_audience` VARCHAR(16) NOT NULL DEFAULT 'all'
        COMMENT '适用对象：all / undergrad / postgrad / teacher / staff';

-- 3. 课程学期（用于硬过滤中的"操作系统2025春"）
ALTER TABLE `resource`
    ADD COLUMN `course_term` VARCHAR(128) NOT NULL DEFAULT ''
        COMMENT '课程+学期，如：操作系统2025春';

-- 4. 关键词列表（AI 抽取，用于 RAG 召回权重加成）
ALTER TABLE `resource`
    ADD COLUMN `keywords` JSON NOT NULL DEFAULT '[]'
        COMMENT '关键词数组 [{"word": "操作系统", "weight": 10}, ...]';

-- 5. 索引（仅给硬过滤字段建索引，keywords 不建索引）
ALTER TABLE `resource` ADD INDEX `idx_res_visibility` (`visibility`);
ALTER TABLE `resource` ADD INDEX `idx_res_course_term` (`course_term`);
```

> **说明**：
> - `visibility` 和 `course_term` 需要索引（硬过滤做 WHERE）
> - `keywords` 不建索引（仅做权重加成扫描，单文档内容少）
> - `target_audience` 不建索引（基数小，全表扫描代价低）

---

## 二、回滚 SQL

```sql
ALTER TABLE `resource` DROP INDEX `idx_res_course_term`;
ALTER TABLE `resource` DROP INDEX `idx_res_visibility`;
ALTER TABLE `resource` DROP COLUMN `keywords`;
ALTER TABLE `resource` DROP COLUMN `course_term`;
ALTER TABLE `resource` DROP COLUMN `target_audience`;
ALTER TABLE `resource` DROP COLUMN `visibility`;
```

---

## 三、字段业务含义

| 字段 | 业务映射 | 检索层 | 类型 |
|------|---------|--------|------|
| `visibility` | 普通用户只能看 public/internal，restricted 仅作者可见 | 硬过滤（WHERE） + 权限校验 | `VARCHAR(16)` |
| `target_audience` | 本科生只能看 undergrad/all，研究生可看 postgrad/all | 硬过滤（WHERE） | `VARCHAR(16)` |
| `course_term` | 课程名+学期，是高频硬过滤条件 | 硬过滤（LIKE） | `VARCHAR(128)` |
| `keywords` | AI 自动抽取，每条资源 5–15 个关键词 + 权重 | 软匹配权重加成 | `JSON` |

---

## 四、Migration 文件

将生成以下文件：

```
backend/apps/resources/migrations/0002_resource_keywords.py
```

包含：

1. `AddField(visibility)`
2. `AddField(target_audience)`
3. `AddField(course_term)`
4. `AddField(keywords)`（JSON Field，MySQL 后端存 `LONGTEXT`）
5. `AddIndex(visibility)`
6. `AddIndex(course_term)`

---

## 五、数据迁移策略

新增字段均带默认值，**不需要跑数据回填脚本**：

| 字段 | 默认值 | 实际影响 |
|------|--------|---------|
| `visibility` | `'public'` | 已有资源默认公开，可后续手动改为 `internal` |
| `target_audience` | `'all'` | 已有资源默认适用全部人 |
| `course_term` | `''` | 已有资源需要重新标注 |
| `keywords` | `'[]'` | 已有资源需要 AI 重抽取 |

> **后续动作（不在本次变更范围）**：
> - 提供管理后台 UI，让上传员手动补全 `course_term`
> - 提供 Celery 异步任务，AI 重抽取已有资源的 `keywords`
