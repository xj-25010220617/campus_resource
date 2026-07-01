-- ============================================================
-- 为所有自定义表添加中文注释
-- 生成时间: 2026-07-01
-- ============================================================

-- ----------------------------
-- 表注释
-- ----------------------------
ALTER TABLE `resource` COMMENT '资源表';
ALTER TABLE `user` COMMENT '用户表';
ALTER TABLE `college` COMMENT '学院表';
ALTER TABLE `discipline` COMMENT '学科方向表';
ALTER TABLE `category` COMMENT '资源分类表';
ALTER TABLE `favorite` COMMENT '收藏表';
ALTER TABLE `query_log` COMMENT '查询日志表（RAG对话）';
ALTER TABLE `chat_conversation` COMMENT '会话表';
ALTER TABLE `chat_message` COMMENT '消息表';
ALTER TABLE `chunk` COMMENT '文档分块表（RAG向量化用的chunk，Milvus同步）';

-- ----------------------------
-- resource 表
-- ----------------------------
ALTER TABLE `resource`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `title` VARCHAR(256) NOT NULL COMMENT '资源标题',
  MODIFY COLUMN `description` LONGTEXT NOT NULL COMMENT '资源描述',
  MODIFY COLUMN `oss_key` VARCHAR(512) NOT NULL COMMENT 'OSS对象Key',
  MODIFY COLUMN `oss_url` VARCHAR(1024) NOT NULL COMMENT 'OSS访问URL',
  MODIFY COLUMN `file_name` VARCHAR(256) NOT NULL COMMENT '原始文件名',
  MODIFY COLUMN `file_size` BIGINT NOT NULL COMMENT '文件大小（字节）',
  MODIFY COLUMN `file_type` VARCHAR(16) NOT NULL COMMENT '文件类型',
  MODIFY COLUMN `md5_hash` VARCHAR(32) NOT NULL COMMENT 'MD5哈希（去重）',
  MODIFY COLUMN `status` VARCHAR(16) NOT NULL COMMENT '状态',
  MODIFY COLUMN `audit_comment` LONGTEXT NOT NULL COMMENT '审核意见',
  MODIFY COLUMN `view_count` INT NOT NULL COMMENT '浏览量',
  MODIFY COLUMN `download_count` INT NOT NULL COMMENT '下载量',
  MODIFY COLUMN `created_at` DATETIME(6) NOT NULL COMMENT '创建时间',
  MODIFY COLUMN `updated_at` DATETIME(6) NOT NULL COMMENT '更新时间',
  MODIFY COLUMN `published_at` DATETIME(6) NULL COMMENT '发布时间',
  MODIFY COLUMN `auditor_id` BIGINT NULL COMMENT '审核人',
  MODIFY COLUMN `category_id` BIGINT NOT NULL COMMENT '资源类型',
  MODIFY COLUMN `college_id` BIGINT NOT NULL COMMENT '所属学院',
  MODIFY COLUMN `discipline_id` BIGINT NOT NULL COMMENT '学科方向',
  MODIFY COLUMN `uploader_id` BIGINT NOT NULL COMMENT '上传者',
  MODIFY COLUMN `visibility` VARCHAR(16) NOT NULL COMMENT '可见层级',
  MODIFY COLUMN `target_audience` VARCHAR(16) NOT NULL COMMENT '适用对象',
  MODIFY COLUMN `course_term` VARCHAR(128) NOT NULL COMMENT '课程学期',
  MODIFY COLUMN `keywords` JSON NOT NULL COMMENT '关键词列表',
  MODIFY COLUMN `chunk_count` INT NOT NULL COMMENT '分块数量（RAG）';

-- ----------------------------
-- user 表
-- ----------------------------
ALTER TABLE `user`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `password` VARCHAR(128) NOT NULL COMMENT '密码',
  MODIFY COLUMN `last_login` DATETIME(6) NULL COMMENT '最后登录时间',
  MODIFY COLUMN `is_superuser` TINYINT(1) NOT NULL COMMENT '是否超级管理员',
  MODIFY COLUMN `username` VARCHAR(150) NOT NULL COMMENT '用户名',
  MODIFY COLUMN `first_name` VARCHAR(150) NOT NULL COMMENT '名',
  MODIFY COLUMN `last_name` VARCHAR(150) NOT NULL COMMENT '姓',
  MODIFY COLUMN `email` VARCHAR(254) NOT NULL COMMENT '邮箱',
  MODIFY COLUMN `is_staff` TINYINT(1) NOT NULL COMMENT '是否可登录后台',
  MODIFY COLUMN `is_active` TINYINT(1) NOT NULL COMMENT '是否激活',
  MODIFY COLUMN `date_joined` DATETIME(6) NOT NULL COMMENT '注册时间',
  MODIFY COLUMN `role` VARCHAR(16) NOT NULL COMMENT '角色',
  MODIFY COLUMN `phone` VARCHAR(11) NOT NULL COMMENT '手机号',
  MODIFY COLUMN `avatar` VARCHAR(200) NOT NULL COMMENT '头像URL',
  MODIFY COLUMN `college_id` BIGINT NULL COMMENT '所属学院';

-- ----------------------------
-- college 表
-- ----------------------------
ALTER TABLE `college`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `name` VARCHAR(64) NOT NULL COMMENT '学院名称',
  MODIFY COLUMN `code` VARCHAR(16) NOT NULL COMMENT '学院代码',
  MODIFY COLUMN `sort_order` INT NOT NULL COMMENT '排序号';

-- ----------------------------
-- discipline 表
-- ----------------------------
ALTER TABLE `discipline`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `name` VARCHAR(64) NOT NULL COMMENT '学科名称',
  MODIFY COLUMN `category` VARCHAR(16) NOT NULL COMMENT '学科分类',
  MODIFY COLUMN `sort_order` INT NOT NULL COMMENT '排序号';

-- ----------------------------
-- category 表
-- ----------------------------
ALTER TABLE `category`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `name` VARCHAR(64) NOT NULL COMMENT '类型名称',
  MODIFY COLUMN `sort_order` INT NOT NULL COMMENT '排序号',
  MODIFY COLUMN `parent_id` BIGINT NULL COMMENT '上级分类';

-- ----------------------------
-- favorite 表
-- ----------------------------
ALTER TABLE `favorite`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `created_at` DATETIME(6) NOT NULL COMMENT '收藏时间',
  MODIFY COLUMN `resource_id` BIGINT NOT NULL COMMENT '资源ID',
  MODIFY COLUMN `user_id` BIGINT NOT NULL COMMENT '用户ID';

-- ----------------------------
-- query_log 表
-- ----------------------------
ALTER TABLE `query_log`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `question` LONGTEXT NOT NULL COMMENT '查询问题',
  MODIFY COLUMN `parsed_filters` JSON NULL COMMENT '解析后的筛选条件',
  MODIFY COLUMN `hit_count` INT NOT NULL COMMENT '命中数量',
  MODIFY COLUMN `top_doc_ids` JSON NULL COMMENT 'Top文档ID列表',
  MODIFY COLUMN `response_ms` INT NOT NULL COMMENT '响应耗时（毫秒）',
  MODIFY COLUMN `user_feedback` SMALLINT NOT NULL COMMENT '用户反馈（1好/0差/-1未反馈）',
  MODIFY COLUMN `created_at` DATETIME(6) NOT NULL COMMENT '创建时间',
  MODIFY COLUMN `conversation_id` BIGINT NULL COMMENT '会话ID',
  MODIFY COLUMN `user_id` BIGINT NOT NULL COMMENT '用户ID';

-- ----------------------------
-- chat_conversation 表
-- ----------------------------
ALTER TABLE `chat_conversation`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `title` VARCHAR(256) NOT NULL COMMENT '会话标题',
  MODIFY COLUMN `created_at` DATETIME(6) NOT NULL COMMENT '创建时间',
  MODIFY COLUMN `updated_at` DATETIME(6) NOT NULL COMMENT '更新时间',
  MODIFY COLUMN `user_id` BIGINT NOT NULL COMMENT '用户ID';

-- ----------------------------
-- chat_message 表
-- ----------------------------
ALTER TABLE `chat_message`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `role` VARCHAR(16) NOT NULL COMMENT '角色（user/assistant）',
  MODIFY COLUMN `content` LONGTEXT NOT NULL COMMENT '消息内容',
  MODIFY COLUMN `references` JSON NOT NULL COMMENT '引用资源列表',
  MODIFY COLUMN `created_at` DATETIME(6) NOT NULL COMMENT '创建时间',
  MODIFY COLUMN `conversation_id` BIGINT NOT NULL COMMENT '会话ID';

-- ----------------------------
-- chunk 表
-- ----------------------------
ALTER TABLE `chunk`
  MODIFY COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  MODIFY COLUMN `resource_id` BIGINT NOT NULL COMMENT '所属资源ID',
  MODIFY COLUMN `chunk_index` INT NOT NULL COMMENT '分块序号（从0开始）',
  MODIFY COLUMN `chunk_text` LONGTEXT NOT NULL COMMENT '分块文本内容',
  MODIFY COLUMN `page_number` INT NOT NULL DEFAULT 0 COMMENT '页码',
  MODIFY COLUMN `seq_order` INT NOT NULL DEFAULT 0 COMMENT '顺序号',
  MODIFY COLUMN `vector_id` VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'Milvus向量ID',
  MODIFY COLUMN `status` VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '同步状态',
  MODIFY COLUMN `char_count` INT NOT NULL DEFAULT 0 COMMENT '字符数',
  MODIFY COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  MODIFY COLUMN `indexed_at` DATETIME NULL COMMENT '入库时间';

-- ============================================================
-- 执行完成
-- ============================================================
