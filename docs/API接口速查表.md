# API 接口速查表

### 角色定义

> | 角色       | 英文标识      | 担任者              | 核心权限                                                     |
> | ---------- | ------------- | ------------------- | ------------------------------------------------------------ |
> | **超管**   | `super_admin` | 信息化中心 1-2 人   | **全部权限**：管理所有用户 + 编辑/删除所有文件 + 查看/下载所有源文件 + 上传资源 + 管理关键词 |
> | **管理员** | `admin`       | 各学院指定 1-2 人   | 管理普通用户 + **编辑/删除自己上传的文件** + 查看/下载自己上传的源文件 + 上传资源 + 管理自己上传文件的关键词 |
> | **用户**   | `user`        | 全校教师 + 全校学生 | 提问获取 AI 回答 + 收藏问答 + 点赞/点踩反馈 + 查看热门问题广场 |

## 一、认证

| 接口 | 用途 |
|------|------|
| `GET /api/auth/sso/login/` | SSO 登录跳转，重定向至学校统一认证页面 |
| `POST /api/auth/sso/callback/` | SSO 回调，签发 JWT |
| `POST /api/auth/login/` | JWT 兜底登录（SSO 未对接时使用） |
| `POST /api/auth/refresh/` | 刷新 Token |
| `GET /api/auth/me/` | 获取当前用户信息（角色+学院） |
| `PUT /api/auth/me/` | 更新个人资料（手机/头像） |
| `POST /api/auth/change-password/` | 用户自己修改密码（需旧密码） |

## 二、基础数据查询

| 接口 | 用途 |
|------|------|
| `GET /api/colleges/` | 学院列表（13个二级学院） |
| `GET /api/disciplines/` | 学科方向列表（可按门类筛选） |
| `GET /api/categories/` | ⚠️ 已废弃：资源类型列表（资源分类系统已移除） |

## 三、智能问答（核心）

| 接口 | 用途 |
|------|------|
| `POST /api/chat/ask/` | **SSE 流式提问**，AI 逐字返回回答 |
| `GET /api/chat/conversations/` | 我的历史会话列表（分页） |
| `GET /api/chat/conversations/{id}/` | 某个会话的完整消息历史 |
| `PATCH /api/chat/conversations/{id}/` | 编辑会话标题 / 切换收藏 |
| `DELETE /api/chat/conversations/{id}/` | 删除会话（级联删除消息） |
| `POST /api/chat/message/{id}/rate/` | 点赞/点踩反馈 |
| `GET /api/chat/message/{id}/references/` | 查看引用来源+下载链接（**仅授权用户**） |

## 四、知识库管理

| 接口 | 用途 |
|------|------|
| `POST /api/knowledge/upload/sts/` | 获取 OSS 直传 STS 凭证（含 MD5 去重检查） |
| `POST /api/knowledge/upload/callback/` | OSS 上传完成回调，创建文档记录并触发索引 |
| `GET /api/knowledge/docs/` | 文档列表（分页+搜索+多条件筛选） |
| `GET /api/knowledge/docs/{id}/` | 文档详情 |
| `PUT /api/knowledge/docs/{id}/` | 编辑文档元数据 |
| `DELETE /api/knowledge/docs/{id}/` | 删除文档（OSS+Milvus+MySQL 三处清理） |
| `GET /api/knowledge/docs/{id}/download/` | 下载源文件（签名 URL，**仅授权用户**） |

## 五、关键词管理

| 接口 | 用途 |
|------|------|
| `GET /api/knowledge/docs/{doc_id}/keywords/` | 某文档的关键词列表 |
| `POST /api/knowledge/docs/{doc_id}/keywords/` | 添加关键词 |
| `PUT /api/knowledge/keywords/{id}/` | 编辑关键词 |
| `DELETE /api/knowledge/keywords/{id}/` | 删除关键词 |

## 六、人工兜底

| 接口 | 用途 |
|------|------|
| `GET /api/feedback/pending/` | 待人工回答的问题列表（按学院） |
| `POST /api/feedback/{id}/answer/` | 提交人工回答，自动索引入知识库 |

## 七、同步管理（二期）

| 接口 | 用途 |
|------|------|
| `POST /api/sync/website/` | 触发官网爬虫同步 |
| `POST /api/sync/library/` | 触发图书馆摘要同步 |
| `GET /api/sync/logs/` | 同步日志列表 |

## 八、管理后台

| 接口 | 用途 |
|------|------|
| `GET /api/admin/users/` | 用户列表（分页+筛选） |
| `POST /api/admin/users/` | 新建用户 |
| `PUT /api/admin/users/{id}/` | 编辑用户（角色/学院/启禁用） |
| `DELETE /api/admin/users/{id}/` | 删除用户 |
| `POST /api/admin/users/{id}/reset-password/` | 管理员重置用户密码 |
| `GET/POST /api/admin/colleges/` | 学院列表+新增 |
| `PUT/DELETE /api/admin/colleges/{id}/` | 编辑/删除学院 |
| `GET/POST /api/admin/disciplines/` | 学科列表+新增 |
| `PUT/DELETE /api/admin/disciplines/{id}/` | 编辑/删除学科 |
| `GET/POST /api/admin/categories/` | 资源类型列表+新增 |
| `PUT/DELETE /api/admin/categories/{id}/` | 编辑/删除资源类型 |

---

> 完整参数和返回字段详见 [API文档.md](API文档.md)
