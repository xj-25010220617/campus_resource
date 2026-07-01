-- ============================================================
-- chunk 表设计说明（参考 backend/apps/resources/models.py）
-- chunk_index 状态枚举: pending / embedding / indexed / failed
-- ============================================================

-- 1. Resource 新增 chunk_count 字段
ALTER TABLE `resource`
  ADD COLUMN `chunk_count` INT NOT NULL DEFAULT 0 COMMENT '分块数量（RAG）';

-- 2. 新建 chunk 表
CREATE TABLE `chunk` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  `resource_id` BIGINT NOT NULL COMMENT '所属资源ID',
  `chunk_index` INT NOT NULL COMMENT '分块序号（从0开始）',
  `chunk_text` LONGTEXT NOT NULL COMMENT '分块文本内容',
  `page_number` INT NOT NULL DEFAULT 0 COMMENT '页码',
  `seq_order` INT NOT NULL DEFAULT 0 COMMENT '顺序号',
  `vector_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'Milvus向量ID',
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '同步状态',
  `char_count` INT NOT NULL DEFAULT 0 COMMENT '字符数',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `indexed_at` DATETIME NULL COMMENT '入库时间',

  -- 外键（级联删除：资源删除时自动删除关联chunks）
  CONSTRAINT `fk_chunk_resource` FOREIGN KEY (`resource_id`)
    REFERENCES `resource`(`id`) ON DELETE CASCADE,

  -- 唯一约束：同一资源下 chunk_index 不重复
  CONSTRAINT `uk_chunk_res_index` UNIQUE (`resource_id`, `chunk_index`),

  -- 索引
  INDEX `idx_chunk_res_index` (`resource_id`, `chunk_index`),
  INDEX `idx_chunk_vector` (`vector_id`),
  INDEX `idx_chunk_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='文档分块表（RAG向量化用的chunk，Milvus同步）';

-- 3. Resource 索引补全（如果有需要可以忽略）
-- 已有的索引：idx_res_disc_college, idx_res_status_created,
--             idx_res_uploader_created, idx_res_visibility,
--             idx_res_course_term
