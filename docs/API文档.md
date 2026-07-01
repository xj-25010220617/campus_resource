# 成都东软学院 AI 知识库智能问答系统 — API 接口文档

> 基础路径：`http://{host}/api`  
> 认证方式：JWT（Header: `Authorization: Bearer <token>`）

---

## 一、认证模块

### 1.1 SSO 登录跳转

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/sso/login/` |
| **请求方式** | `GET` |
| **请求参数** | 无 |
| **请求参数类型** | — |
| **是否必须传入** | — |
| **返回参数** | HTTP 302 重定向至学校统一认证页面 |
| **返回参数类型** | — |
| **备注** | 用户被重定向到学校 SSO 登录页，登录成功后回调 `/api/auth/sso/callback/` |

---

### 1.2 SSO 登录回调

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/sso/callback/` |
| **请求方式** | `POST` |
| **请求参数** | `saml_response` / `code` |
| **请求参数类型** | `string` (form-data) |
| **是否必须传入** | 是 |
| **返回参数** | `access`, `refresh`, `user` |
| **返回参数类型** | `access`: string, `refresh`: string, `user`: object |
| **备注** | SSO 回调后签发 JWT，`user` 对象包含 id/username/role/college_id。MVP 阶段如未对接 SSO，可用 JWT 登录兜底 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `access` | string | access token，有效期 2 小时 |
| `refresh` | string | refresh token，有效期 7 天 |
| `user.id` | int | 用户 ID |
| `user.username` | string | 用户名（工号/学号） |
| `user.role` | string | super_admin / admin / user |
| `user.role_display` | string | 角色中文名 |
| `user.college_id` | int/null | 所属学院 ID |
| `user.college_name` | string/null | 所属学院名称 |
| `user.phone` | string | 手机号 |
| `user.avatar` | string | 头像 URL |

---

### 1.3 刷新 Token

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/refresh/` |
| **请求方式** | `POST` |
| **请求参数** | `refresh` |
| **请求参数类型** | `string` (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `access` |
| **返回参数类型** | `access`: string |
| **备注** | 使用 refresh token 换取新的 access token |

---

### 1.4 获取当前用户信息

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/me/` |
| **请求方式** | `GET` |
| **请求参数** | 无 |
| **请求参数类型** | — |
| **是否必须传入** | — |
| **返回参数** | `id`, `username`, `role`, `role_display`, `college_id`, `college_name`, `phone`, `avatar`, `email`, `date_joined` |
| **返回参数类型** | 见下表 |
| **备注** | 需携带 JWT，用于前端获取当前登录用户信息 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 用户 ID |
| `username` | string | 用户名（工号/学号） |
| `email` | string | 邮箱 |
| `role` | string | super_admin / admin / user |
| `role_display` | string | 角色中文名 |
| `college_id` | int/null | 所属学院 ID |
| `college_name` | string/null | 所属学院名称 |
| `phone` | string | 手机号 |
| `avatar` | string | 头像 URL |
| `date_joined` | string(datetime) | 注册时间 |

---

### 1.5 更新个人资料

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/me/` |
| **请求方式** | `PUT` |
| **请求参数** | `phone`, `avatar` |
| **请求参数类型** | `phone`: string, `avatar`: string(URL) |
| **是否必须传入** | 否（按需传入） |
| **返回参数** | 同 GET `/api/auth/me/` |
| **返回参数类型** | 同上 |
| **备注** | 仅允许修改自己的 phone 和 avatar |

---

### 1.7 修改密码

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/change-password/` |
| **请求方式** | `POST` |
| **请求参数** | `old_password`, `new_password` |
| **请求参数类型** | `old_password`: string, `new_password`: string (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "密码修改成功"}` |
| **返回参数类型** | `object` |
| **备注** | 用户自己修改密码，需提供旧密码验证 |

---

### 1.8 JWT 兜底登录（MVP 阶段，SSO 未对接时使用）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/auth/login/` |
| **请求方式** | `POST` |
| **请求参数** | `username`, `password` |
| **请求参数类型** | `username`: string, `password`: string (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `access`, `refresh`, `user` |
| **返回参数类型** | 同 1.2 SSO 回调 |
| **备注** | SSO 对接后此接口可保留作为管理员后台登录兜底 |

---

## 二、分类查询模块

### 2.1 学院列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/colleges/` |
| **请求方式** | `GET` |
| **请求参数** | 无 |
| **请求参数类型** | — |
| **是否必须传入** | — |
| **返回参数** | `[{id, name, code, sort_order}]` |
| **返回参数类型** | `array[object]` |
| **备注** | 返回 13 个二级学院，按 sort_order 排序 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 学院 ID |
| `name` | string | 学院名称 |
| `code` | string | 学院代码 |
| `sort_order` | int | 排序序号 |

---

### 2.2 学科方向列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/disciplines/` |
| **请求方式** | `GET` |
| **请求参数** | `category` |
| **请求参数类型** | `string` (query) |
| **是否必须传入** | 否（不传返回全部，传值按门类筛选：工学/管理学/艺术学/文学/医学） |
| **返回参数** | `[{id, name, category, sort_order}]` |
| **返回参数类型** | `array[object]` |
| **备注** | 学科方向列表，按 sort_order 排序 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 学科 ID |
| `name` | string | 学科方向名称 |
| `category` | string | 所属门类 |
| `sort_order` | int | 排序序号 |

---

### 2.3 资源类型列表（树形）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/categories/` |
| **请求方式** | `GET` |
| **请求参数** | 无 |
| **请求参数类型** | — |
| **是否必须传入** | — |
| **返回参数** | `[{id, name, parent, sort_order, children}]` |
| **返回参数类型** | `array[object]` |
| **备注** | 返回两级树形结构，`children` 为子分类数组。一级分类的 `parent` 为 null |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 类型 ID |
| `name` | string | 类型名称 |
| `parent` | int/null | 上级分类 ID |
| `sort_order` | int | 排序序号 |
| `children` | array[object] | 子分类列表（结构同父级，children 为空数组） |

---

## 三、智能问答模块（核心）

### 3.1 提问（SSE 流式）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/ask/` |
| **请求方式** | `POST` |
| **请求参数** | `question`, `conversation_id` |
| **请求参数类型** | `question`: string, `conversation_id`: int (JSON body) |
| **是否必须传入** | `question`: 是, `conversation_id`: 否 |
| **返回参数** | SSE 流式事件流 |
| **返回参数类型** | SSE (text/event-stream) |
| **备注** | 核心接口。不传 `conversation_id` 则自动创建新会话。返回 SSE 流，前端逐字渲染 |

**SSE 事件类型：**

| 事件名 | 数据内容 | 说明 |
|--------|---------|------|
| `start` | `{"conversation_id": 123, "message_id": 456}` | 流开始，返回会话ID和消息ID |
| `token` | `{"content": "软件工程"}` | 逐 token 输出 AI 回答文本 |
| `references` | `{"count": 5, "summary": "参考了5份文档"}` | 普通用户收到引用计数 |
| `references_detail` | `[{"doc_title":"...", "page":3, "score":0.92, "download_url":"..."}]` | 授权用户收到完整引用列表含下载链接 |
| `suggested` | `["问题1", "问题2", "问题3"]` | 推荐后续问题 |
| `confidence` | `{"score": 0.88}` | 置信度 |
| `done` | `{"tokens_used": 1234}` | 流结束 |
| `error` | `{"message": "错误信息"}` | 出错 |

---

### 3.2 我的会话列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/conversations/` |
| **请求方式** | `GET` |
| **请求参数** | `page`, `page_size` |
| **请求参数类型** | `page`: int, `page_size`: int (query) |
| **是否必须传入** | 否（默认 page=1, page_size=20） |
| **返回参数** | `{count, next, previous, results: [{id, title, message_count, is_favorite, created_at, updated_at}]}` |
| **返回参数类型** | `object` (分页) |
| **备注** | 按 updated_at 倒序，仅返回当前用户的会话 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `count` | int | 总条数 |
| `next` | string/null | 下一页 URL |
| `previous` | string/null | 上一页 URL |
| `results[].id` | int | 会话 ID |
| `results[].title` | string | 会话标题 |
| `results[].message_count` | int | 消息数量 |
| `results[].is_favorite` | bool | 是否收藏 |
| `results[].created_at` | string(datetime) | 创建时间 |
| `results[].updated_at` | string(datetime) | 最后活跃时间 |

---

### 3.3 会话消息详情

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/conversations/{id}/` |
| **请求方式** | `GET` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{id, title, messages: [{id, role, content, rating, created_at}]}` |
| **返回参数类型** | `object` |
| **备注** | 返回会话元数据 + 完整消息列表，只能查看自己的会话 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 会话 ID |
| `title` | string | 会话标题 |
| `messages[].id` | int | 消息 ID |
| `messages[].role` | string | user / assistant |
| `messages[].content` | string | 消息内容（Markdown） |
| `messages[].suggested_questions` | array/null | 推荐问题（仅 assistant） |
| `messages[].rating` | int/null | 用户反馈 1/0 |
| `messages[].created_at` | string(datetime) | 消息时间 |

---

### 3.4 编辑会话

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/conversations/{id}/` |
| **请求方式** | `PATCH` |
| **请求参数** | `title`, `is_favorite` |
| **请求参数类型** | `title`: string, `is_favorite`: bool (JSON body) |
| **是否必须传入** | 否（按需传入） |
| **返回参数** | 同 3.3 |
| **返回参数类型** | `object` |
| **备注** | 可重命名会话标题、切换收藏状态 |

---

### 3.5 删除会话

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/conversations/{id}/` |
| **请求方式** | `DELETE` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "删除成功"}` |
| **返回参数类型** | `object` |
| **备注** | 级联删除该会话下所有消息 |

---

### 3.6 消息反馈（点赞/点踩）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/message/{id}/rate/` |
| **请求方式** | `POST` |
| **请求参数** | `rating`, `reason` |
| **请求参数类型** | `rating`: int (1=点赞, 0=点踩), `reason`: string (JSON body) |
| **是否必须传入** | `rating`: 是, `reason`: 否 |
| **返回参数** | `{"detail": "反馈成功"}` |
| **返回参数类型** | `object` |
| **备注** | 仅允许对自己会话中的 assistant 消息进行反馈。rating=0 时建议填写 reason |

---

### 3.7 查看引用来源（授权用户）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/chat/message/{id}/references/` |
| **请求方式** | `GET` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `[{doc_title, doc_id, page, score, content_snippet, download_url}]` |
| **返回参数类型** | `array[object]` |
| **备注** | **仅 super_admin 和 admin 可调用**。admin 只能看自己上传的文档引用，super_admin 可看全部 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `doc_title` | string | 文档标题 |
| `doc_id` | int | 文档 ID |
| `page` | int/null | 页码 |
| `score` | float | 匹配度（0-1） |
| `content_snippet` | string | 匹配片段摘要 |
| `download_url` | string | OSS 签名下载 URL（有效期 300 秒） |

---

## 四、知识库管理模块

### 4.1 获取 OSS 上传凭证

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/upload/sts/` |
| **请求方式** | `POST` |
| **请求参数** | `file_name`, `file_size`, `md5` |
| **请求参数类型** | `file_name`: string, `file_size`: int, `md5`: string (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `{oss_key, upload_url, sts_credentials}` 或 `{duplicate: true, resource_id}` |
| **返回参数类型** | `object` |
| **备注** | **仅 admin 和 super_admin 可调用**。后端先查 MD5 去重，已存在则返回 duplicate=true，前端跳过上传直接用已有资源 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `oss_key` | string | OSS 对象 Key |
| `upload_url` | string | OSS 直传地址 |
| `sts_credentials` | object | 临时凭证（AccessKeyId/SecretAccessKey/SecurityToken） |
| `duplicate` | bool | 是否重复（true时返回已有 resource_id） |
| `resource_id` | int | 重复时的已有资源 ID |

---

### 4.2 上传完成回调

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/upload/callback/` |
| **请求方式** | `POST` |
| **请求参数** | `oss_key`, `title`, `description`, `file_name`, `file_size`, `file_type`, `md5`, `discipline_id`, `category_id`, `college_id` |
| **请求参数类型** | 见下表 (JSON body) |
| **是否必须传入** | 带 * 必填，其余选填 |
| **返回参数** | `{id, status}` |
| **返回参数类型** | `object` |
| **备注** | OSS 上传成功后前端调用，创建 knowledge_doc 记录并自动触发 Celery 异步索引任务 |

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|:--:|------|
| `oss_key` | string | * | OSS 对象 Key |
| `title` | string | * | 文档标题 |
| `description` | string | * | 文档描述（不可提取文本格式时尤为重要） |
| `file_name` | string | * | 原始文件名 |
| `file_size` | int | * | 文件大小（字节） |
| `file_type` | string | * | pdf/doc/ppt/xls/image/video/audio/md/txt/html/other |
| `md5` | string | * | MD5 哈希 |
| `discipline_id` | int | | 学科方向 ID |
| `category_id` | int | | 资源类型 ID |
| `college_id` | int | | 所属学院 ID（默认取上传者学院） |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 新创建的文档 ID |
| `status` | string | uploaded（等待处理） |

---

### 4.3 文档列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/` |
| **请求方式** | `GET` |
| **请求参数** | `page`, `page_size`, `search`, `college_id`, `discipline_id`, `category_id`, `file_type`, `status`, `source_type` |
| **请求参数类型** | 全为 query string |
| **是否必须传入** | 否（全部可选） |
| **返回参数** | `{count, next, previous, results: [{id, title, file_type, file_size, status, college_name, discipline_name, uploader_name, chunk_count, created_at}]}` |
| **返回参数类型** | `object` (分页) |
| **备注** | **admin 只能看到自己上传的文档**，super_admin 可看到全部。支持多条件筛选和全文搜索 |

---

### 4.4 文档详情

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{id}/` |
| **请求方式** | `GET` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{id, title, description, file_type, file_size, file_name, status, source_type, source_url, oss_key, oss_url, college, discipline, category, uploader, chunk_count, page_count, is_sensitive, freshness_date, created_at, updated_at, indexed_at}` |
| **返回参数类型** | `object` |
| **备注** | admin 只能查看自己上传的文档详情 |

---

### 4.5 编辑文档元数据

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{id}/` |
| **请求方式** | `PUT` |
| **请求参数** | `title`, `description`, `discipline_id`, `category_id`, `freshness_date` |
| **请求参数类型** | 见下表 (JSON body) |
| **是否必须传入** | 否（按需传入） |
| **返回参数** | 同 4.4 |
| **返回参数类型** | `object` |
| **备注** | admin 只能编辑自己的文档，super_admin 可编辑所有。修改后不触发重新索引 |

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|:--:|------|
| `title` | string | | 文档标题 |
| `description` | string | | 文档描述 |
| `discipline_id` | int | | 学科方向 ID |
| `category_id` | int | | 资源类型 ID |
| `freshness_date` | string(date) | | 内容截止日期 |

---

### 4.6 删除文档

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{id}/` |
| **请求方式** | `DELETE` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "删除成功"}` |
| **返回参数类型** | `object` |
| **备注** | 级联删除：OSS 文件 + Milvus 向量 + MySQL 中 doc_chunk / keyword / knowledge_doc 记录。admin 只能删除自己的文档 |

---

### 4.7 下载源文件（授权用户）

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{id}/download/` |
| **请求方式** | `GET` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{"download_url": "https://oss-cn-xxx.aliyuncs.com/..."}` 或 HTTP 302 重定向 |
| **返回参数类型** | `object` |
| **备注** | **仅 super_admin 和 admin 可调用**。admin 只能下载自己上传的文档。签名 URL 有效期 300 秒 |

---

## 五、关键词管理模块

### 5.1 文档关键词列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{doc_id}/keywords/` |
| **请求方式** | `GET` |
| **请求参数** | 无（路径参数 `doc_id`） |
| **请求参数类型** | `doc_id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `[{id, phrase, match_type, weight, created_by_name, created_at}]` |
| **返回参数类型** | `array[object]` |
| **备注** | 返回该文档所有关键词，按 weight 降序 |

| 返回字段 | 类型 | 说明 |
|----------|------|------|
| `id` | int | 关键词 ID |
| `phrase` | string | 关键词/关键语句 |
| `match_type` | string | exact / fuzzy / semantic |
| `weight` | int | 权重 1-10 |
| `created_by_name` | string | 创建人姓名 |
| `created_at` | string(datetime) | 创建时间 |

---

### 5.2 添加关键词

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/docs/{doc_id}/keywords/` |
| **请求方式** | `POST` |
| **请求参数** | `phrase`, `match_type`, `weight` |
| **请求参数类型** | `phrase`: string, `match_type`: string, `weight`: int (JSON body) |
| **是否必须传入** | `phrase`: 是, `match_type`: 否(默认 fuzzy), `weight`: 否(默认 1) |
| **返回参数** | `{id, phrase, match_type, weight}` |
| **返回参数类型** | `object` |
| **备注** | admin 只能给自己的文档添加。同一文档内 phrase 唯一 |

---

### 5.3 编辑关键词

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/keywords/{id}/` |
| **请求方式** | `PUT` |
| **请求参数** | `phrase`, `match_type`, `weight` |
| **请求参数类型** | 同 5.2 (JSON body) |
| **是否必须传入** | 否（按需传入） |
| **返回参数** | 同 5.2 |
| **返回参数类型** | `object` |
| **备注** | admin 只能编辑自己文档下的关键词 |

---

### 5.4 删除关键词

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/knowledge/keywords/{id}/` |
| **请求方式** | `DELETE` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "删除成功"}` |
| **返回参数类型** | `object` |
| **备注** | admin 只能删除自己文档下的关键词 |

---

## 六、人工兜底模块

### 6.1 待回答列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/feedback/pending/` |
| **请求方式** | `GET` |
| **请求参数** | `page`, `page_size`, `college_id` |
| **请求参数类型** | `page`: int, `page_size`: int, `college_id`: int (query) |
| **是否必须传入** | 否 |
| **返回参数** | `{count, results: [{id, question, status, created_at}]}` |
| **返回参数类型** | `object` (分页) |
| **备注** | admin 只能看本学院的待回答列表，super_admin 看全部 |

---

### 6.2 提交人工回答

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/feedback/{id}/answer/` |
| **请求方式** | `POST` |
| **请求参数** | `answer_text` |
| **请求参数类型** | `answer_text`: string (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `{id, status, answered_at}` |
| **返回参数类型** | `object` |
| **备注** | 状态变为 answered，回答文本自动作为新文档索引入库，供后续检索 |

---

## 七、同步管理模块（第二期，super_admin 专属）

### 7.1 触发官网爬虫

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/sync/website/` |
| **请求方式** | `POST` |
| **请求参数** | `target` |
| **请求参数类型** | `target`: string (JSON body) |
| **是否必须传入** | 否（默认全部站点） |
| **返回参数** | `{sync_log_id, status}` |
| **返回参数类型** | `object` |
| **备注** | target 可选值：`all` / `main`(nsu.edu.cn) / `dean` / `colleges`。触发后异步执行 |

---

### 7.2 触发图书馆同步

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/sync/library/` |
| **请求方式** | `POST` |
| **请求参数** | 无 |
| **请求参数类型** | — |
| **是否必须传入** | — |
| **返回参数** | `{sync_log_id, status}` |
| **返回参数类型** | `object` |
| **备注** | 触发 CNKI/维普/超星摘要同步任务 |

---

### 7.3 同步日志

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/sync/logs/` |
| **请求方式** | `GET` |
| **请求参数** | `page`, `page_size`, `source`, `status` |
| **请求参数类型** | `page`: int, `page_size`: int, `source`: string, `status`: string (query) |
| **是否必须传入** | 否 |
| **返回参数** | `{count, results: [{id, source, target_url, status, docs_found, docs_new, docs_updated, error_message, started_at, finished_at}]}` |
| **返回参数类型** | `object` (分页) |
| **备注** | 可按 source(website/library) 和 status(running/success/failed) 筛选 |

---

## 八、管理后台模块

### 8.1 用户列表

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/admin/users/` |
| **请求方式** | `GET` |
| **请求参数** | `page`, `page_size`, `search`, `role`, `college_id`, `is_active` |
| **请求参数类型** | 全为 query string |
| **是否必须传入** | 否 |
| **返回参数** | `{count, results: [{id, username, role, role_display, college_id, college_name, phone, email, is_active, date_joined}]}` |
| **返回参数类型** | `object` (分页) |
| **备注** | admin 只能查看 user 角色，super_admin 可查看所有角色。密码不返回 |

---

### 8.2 新建用户

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/admin/users/` |
| **请求方式** | `POST` |
| **请求参数** | `username`, `password`, `role`, `college_id`, `phone`, `email`, `first_name`, `last_name` |
| **请求参数类型** | 见下表 (JSON body) |
| **是否必须传入** | `username`*, `password`*, `role`* 必填，其余选填 |
| **返回参数** | `{id, username, role, college_id}` |
| **返回参数类型** | `object` |
| **备注** | admin 只能创建 role=user 的账号。super_admin 可创建所有角色 |

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|:--:|------|
| `username` | string | * | 工号/学号 |
| `password` | string | * | 初始密码 |
| `role` | string | * | super_admin / admin / user |
| `college_id` | int | | 所属学院 |
| `phone` | string | | 手机号 |
| `email` | string | | 邮箱 |
| `first_name` | string | | 名 |
| `last_name` | string | | 姓 |

---

### 8.3 编辑用户

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/admin/users/{id}/` |
| **请求方式** | `PUT` |
| **请求参数** | `role`, `college_id`, `phone`, `email`, `is_active`, `first_name`, `last_name` |
| **请求参数类型** | 见下表 (JSON body) |
| **是否必须传入** | 否（按需传入） |
| **返回参数** | 同 8.1 单条 |
| **返回参数类型** | `object` |
| **备注** | admin 只能编辑 user 角色用户。不允许修改 username |

| 请求字段 | 类型 | 必填 | 说明 |
|----------|------|:--:|------|
| `role` | string | | 角色 |
| `college_id` | int | | 学院 |
| `phone` | string | | 手机号 |
| `email` | string | | 邮箱 |
| `is_active` | bool | | 启用/禁用 |
| `first_name` | string | | 名 |
| `last_name` | string | | 姓 |

---

### 8.4 删除用户

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/admin/users/{id}/` |
| **请求方式** | `DELETE` |
| **请求参数** | 无（路径参数 `id`） |
| **请求参数类型** | `id`: int (path) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "删除成功"}` |
| **返回参数类型** | `object` |
| **备注** | admin 只能删除 user 角色。super_admin 可删除 admin 和 user，不能删除自己和其他 super_admin |

---

### 8.4.1 重置用户密码

| 项目 | 内容 |
|------|------|
| **接口路径** | `/api/admin/users/{id}/reset-password/` |
| **请求方式** | `POST` |
| **请求参数** | `new_password` |
| **请求参数类型** | `new_password`: string (JSON body) |
| **是否必须传入** | 是 |
| **返回参数** | `{"detail": "密码已重置"}` |
| **返回参数类型** | `object` |
| **备注** | 管理员强制重置用户密码，无需旧密码。admin 只能重置 user 角色，super_admin 可重置 admin 和 user |

---

### 8.5 学院管理（CRUD）

| 接口路径 | 请求方式 | 说明 | 权限 |
|----------|---------|------|------|
| `/api/admin/colleges/` | `GET` | 学院列表 | super_admin |
| `/api/admin/colleges/` | `POST` | 新建学院 | super_admin |
| `/api/admin/colleges/{id}/` | `PUT` | 编辑学院 | super_admin |
| `/api/admin/colleges/{id}/` | `DELETE` | 删除学院 | super_admin |

**请求参数（POST/PUT）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `name` | string | * | 学院名称 |
| `code` | string | * | 学院代码 |
| `sort_order` | int | | 排序 |

---

### 8.6 学科方向管理（CRUD）

| 接口路径 | 请求方式 | 说明 | 权限 |
|----------|---------|------|------|
| `/api/admin/disciplines/` | `GET` | 学科列表 | super_admin |
| `/api/admin/disciplines/` | `POST` | 新建学科 | super_admin |
| `/api/admin/disciplines/{id}/` | `PUT` | 编辑学科 | super_admin |
| `/api/admin/disciplines/{id}/` | `DELETE` | 删除学科 | super_admin |

**请求参数（POST/PUT）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `name` | string | * | 学科名称 |
| `category` | string | * | 工学/管理学/艺术学/文学/医学 |
| `sort_order` | int | | 排序 |

---

### 8.7 资源类型管理（CRUD）

| 接口路径 | 请求方式 | 说明 | 权限 |
|----------|---------|------|------|
| `/api/admin/categories/` | `GET` | 类型列表（树形） | super_admin |
| `/api/admin/categories/` | `POST` | 新建类型 | super_admin |
| `/api/admin/categories/{id}/` | `PUT` | 编辑类型 | super_admin |
| `/api/admin/categories/{id}/` | `DELETE` | 删除类型 | super_admin |

**请求参数（POST/PUT）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:--:|------|
| `name` | string | * | 类型名称 |
| `parent_id` | int/null | | 上级分类 ID（null 为一级） |
| `sort_order` | int | | 排序 |

---

### 8.8 统计看板（第二期）

> MVP 阶段不做，第二期问答积累量上来后再添加。

---

## 附录

### A. 全局错误响应格式

```json
{
    "detail": "错误描述信息",
    "code": "error_code"
}
```

| HTTP 状态码 | 说明 |
|-------------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 204 | 删除成功（无返回体） |
| 400 | 请求参数错误 |
| 401 | 未认证（Token 过期或无效） |
| 403 | 无权限（角色不满足或数据隔离拦截） |
| 404 | 资源不存在 |
| 409 | 冲突（如 MD5 重复） |
| 500 | 服务器内部错误 |

### B. 鉴权说明

- 所有接口（除 `/api/auth/login/` 和 `/api/auth/sso/*` 外）需携带 JWT
- Header: `Authorization: Bearer <access_token>`
- access_token 过期后用 `/api/auth/refresh/` 刷新
- 权限分级：
  - `user`：仅问答相关接口
  - `admin`：知识库管理（限自己上传）+ 用户管理（限 user 角色）+ 统计（本学院）
  - `super_admin`：全部接口 + 全部数据

### C. 分页格式

```json
{
    "count": 156,
    "next": "http://host/api/resources/?page=2&page_size=20",
    "previous": null,
    "results": [...]
}
```

默认 `page_size=20`，可通过 query 参数覆盖。

---

> 文档版本：v1.0  
> 最后更新：2026-06-30  
> 对应方案：[校园虚拟资源管理设计方案](C:\Users\Aurora\.claude\plans\shimmying-nibbling-naur.md)
