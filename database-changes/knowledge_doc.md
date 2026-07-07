# knowledge_doc 表

> **变更类型**：字段新增
> **变更日期**：2026-07-02 16:19:00
> **变更人员**：Cursor Agent
> **变更原因**：`KnowledgeDoc` 模型需要增加 `visibility` 可见性字段（public/private），支持 RAG 查询时对知识文档做权限过滤。原有 `0001_initial` migration 未包含此字段，远程数据库表结构落后于代码。
> **影响范围**：无数据影响（新增字段有 default='public' 不影响现有记录）
> **回滚方案**：`ALTER TABLE `knowledge_doc` DROP COLUMN `visibility`;`

```sql
ALTER TABLE `knowledge_doc`
ADD COLUMN `visibility` VARCHAR(16) NOT NULL DEFAULT 'public'
COMMENT '可见性: public=公开 / private=私密'
AFTER `file_type`,
ADD INDEX `idx_knowledge_doc_visibility` (`visibility`);
```

---

## 变更 #1：为 `KnowledgeDoc` 添加 `visibility` 可见性字段

> **变更类型**：字段新增
> **变更日期**：2026-07-02 16:19:00
> **变更人员**：Cursor Agent
> **变更原因**：RAG 查询需要对知识文档做权限级别硬过滤，区分公开/私密文档
> **影响范围**：无
> **回滚方案**：`ALTER TABLE `knowledge_doc` DROP COLUMN `visibility`;`

```sql
ALTER TABLE `knowledge_doc`
ADD COLUMN `visibility` VARCHAR(16) NOT NULL DEFAULT 'public'
COMMENT '可见性: public=公开 / private=私密'
AFTER `file_type`,
ADD INDEX `idx_knowledge_doc_visibility` (`visibility`);
```
