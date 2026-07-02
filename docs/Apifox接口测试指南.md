# 校园资源管理系统 — Apifox 接口测试完整指南

> **适用版本**：Apifox 桌面版 2.x+
> **后端地址**：`http://127.0.0.1:8000`
> **日期**：2026-07-02

---

## 一、Apifox 从零开始（新手操作）

### 1.1 新建项目

1. 打开 Apifox → 点击左上角 **"+"** → **"新建项目"**
2. 项目名称填：`校园资源管理系统`
3. 项目图标随便选 → 点 **"新建"**
4. 左侧会出现一个空项目，准备好接接口了

### 1.2 配置环境变量（避免每次手填 URL）

1. 点右上角 **"环境"** 下拉 → **"环境管理"**
2. 点 **"新建环境"**，名称填 `本地开发`
3. 添加以下变量：

| 变量名 | 值 |
|--------|-----|
| `base_url` | `http://127.0.0.1:8000` |
| `token` | *(先留空，登录后更新)* |

4. 点 **"保存"**，右上角切到 `本地开发` 环境
5. 之后所有接口路径填 `/api/knowledge/docs/` 即可，Apifox 会自动拼上 `base_url`

### 1.3 获取 Token（登录鉴权）

**先确认你已经有账号**，然后用以下接口登录：

| 项目 | 值 |
|------|-----|
| 请求方式 | `POST` |
| 路径 | `/api/auth/login/` |
| Body → raw JSON | `{"username": "admin", "password": "your_password"}` |

**成功响应**：
```json
{
    "access": "eyJhbGciOiJI...（长字符串）",
    "refresh": "eyJhbGciOiJI...（长字符串）"
}
```

拿到 `access` 的值后：

1. 回到 Apifox → **"环境管理"** → `token` 变量值填 `eyJhbGciOiJI...`（access 的值）
2. 在需要鉴权的接口中，**Header** 添加一行：
   - 参数名：`Authorization`
   - 参数值：`Bearer {{token}}`

> 之后所有需要登录的接口都加这个 Header，token 过期了就重新登录刷新环境变量。

### 1.4 创建接口文件夹（按模块分组）

在项目左侧根目录右键 → **"新建文件夹"**，创建三个文件夹：

```
├── 知识库管理模块
├── 关键词管理模块
└── 文件上传模块
```

以后新增接口就拖到对应文件夹里，条理清楚。

---

## 二、知识库管理模块（8 个接口）

> **通用 Header**：`Authorization: Bearer {{token}}`

---

### 2.1 文档列表（分页 + 搜索 + 多条件筛选）

| 项目 | 值 |
|------|-----|
| **接口名称** | 知识文档列表 |
| **请求方式** | `GET` |
| **路径** | `/api/knowledge/docs/` |
| **权限** | 所有登录用户 |

**Query 参数**（填在 Params 标签页）：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `page` | int | 否 | 1 | 页码 |
| `page_size` | int | 否 | 20 | 每页条数（建议测试时设 5） |
| `search` | str | 否 | — | 模糊搜索（匹配标题+描述） |
| `college_id` | int | 否 | — | 学院 ID，如 `3` |
| `discipline_id` | int | 否 | — | 学科 ID，如 `5` |
| `category_id` | int | 否 | — | 资源类型 ID，传一级自动展开 |
| `file_type` | str | 否 | — | 文件类型：`pdf`/`doc`/`mp4`/`mp3`… |
| `status` | str | 否 | — | 状态：`indexed`/`uploaded`… |
| `source_type` | str | 否 | — | 来源：`upload`/`website`/`library` |
| `days_back` | int | 否 | 365 | 时间范围（天），`0`=不限 |

**测试用例 A** — 基础分页：
```
GET /api/knowledge/docs/?page=1&page_size=5
```

**测试用例 B** — 关键词搜索 + 学院 + 时间：
```
GET /api/knowledge/docs/?search=软件工程&college_id=1&days_back=30
```

**成功响应**（200）：
```json
{
    "count": 42,
    "next": "http://127.0.0.1:8000/api/knowledge/docs/?page=2&page_size=5",
    "previous": null,
    "results": [
        {
            "id": 1,
            "title": "软件工程导论课件",
            "file_type": "pdf",
            "file_size": 2048000,
            "status": "indexed",
            "college_name": "计算机与软件学院",
            "discipline_name": "软件工程",
            "uploader_name": "admin",
            "chunk_count": 15,
            "created_at": "2026-06-15T10:30:00Z"
        }
    ]
}
```

**Apifox 后置断言**（写在 "后置操作" → "自定义脚本"）：
```javascript
// 状态码断言
pm.test("状态码 200", () => pm.response.to.have.status(200));

// 响应体断言
const json = pm.response.json();
pm.test("包含 count 字段", () => pm.expect(json).to.have.property("count"));
pm.test("包含 results 数组", () => pm.expect(json.results).to.be.an("array"));
pm.test("分页不超 page_size", () => pm.expect(json.results.length).to.be.at.most(5));
pm.test("每条含 id/title/file_type", () => {
    json.results.forEach(item => {
        pm.expect(item).to.have.property("id");
        pm.expect(item).to.have.property("title");
        pm.expect(item).to.have.property("file_type");
    });
});
```

---

### 2.2 文档详情

| 项目 | 值 |
|------|-----|
| **接口名称** | 知识文档详情 |
| **请求方式** | `GET` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/` |
| **权限** | 所有登录用户 |

**前置操作**：先跑 2.1 接口拿一个 `id`，填入路径。

**成功响应**（200）：
```json
{
    "id": 1,
    "title": "软件工程导论课件",
    "description": "计算机学院软件工程专业入门教材...",
    "source_type": "upload",
    "source_url": "",
    "oss_key": "knowledge/1/2026-07/a1b2c3d4e5f6.pdf",
    "oss_url": "https://bucket.oss-cn-hangzhou.aliyuncs.com/knowledge/1/...",
    "file_name": "软件工程导论.pdf",
    "file_type": "pdf",
    "file_size": 2048000,
    "md5_hash": "d41d8cd98f00b204e9800998ecf8427e",
    "page_count": 0,
    "discipline": 1,
    "discipline_name": "软件工程",
    "category": 5,
    "category_name": "教学课件",
    "college": 1,
    "college_name": "计算机与软件学院",
    "uploader": 2,
    "uploader_name": "admin",
    "status": "indexed",
    "chunk_count": 15,
    "is_sensitive": false,
    "sensitive_checked": true,
    "freshness_date": null,
    "created_at": "2026-06-15T10:30:00Z",
    "updated_at": "2026-06-16T08:00:00Z",
    "indexed_at": "2026-06-16T08:00:00Z"
}
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("含完整字段", () => {
    pm.expect(json).to.have.property("id");
    pm.expect(json).to.have.property("title");
    pm.expect(json).to.have.property("oss_key");
    pm.expect(json).to.have.property("file_type");
    pm.expect(json).to.have.property("college_name");
    pm.expect(json).to.have.property("discipline_name");
    pm.expect(json).to.have.property("uploader_name");
    pm.expect(json).to.have.property("visibility");
});
```

---

### 2.3 编辑文档元数据

| 项目 | 值 |
|------|-----|
| **接口名称** | 编辑文档元数据 |
| **请求方式** | `PUT` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/` |
| **权限** | 超管可编辑全部，其他仅限自己上传 |

**Body → raw JSON**：
```json
{
    "title": "软件工程导论课件（2026春修订）",
    "description": "更新了第5章内容，补充了敏捷开发实践案例",
    "discipline_id": 1,
    "category_id": 5,
    "freshness_date": "2026-07-02"
}
```

> 全部字段可选，只传要改的。不改的字段省略即可。

**成功响应**（200）：返回更新后的完整文档对象（同 2.2 的响应格式）。

**错误场景**：非上传者编辑他人文档 → 403：
```json
{ "detail": "只能编辑自己上传的文档" }
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("标题已更新", () => pm.expect(json.title).to.include("修订"));
```

---

### 2.4 删除文档

| 项目 | 值 |
|------|-----|
| **接口名称** | 删除知识文档 |
| **请求方式** | `DELETE` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/delete/` |
| **权限** | `IsOwnerOrReadOnly`（超管全删，其余仅删自己） |

**成功响应**（200）：
```json
{ "detail": "删除成功" }
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
pm.test("删除成功", () => pm.response.json().detail === "删除成功");
```

---

### 2.5 下载文档（签名 URL）

| 项目 | 值 |
|------|-----|
| **接口名称** | 下载文档 |
| **请求方式** | `GET` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/download/` |
| **权限** | 超管全下，其余仅下自己上传 |

**成功响应**（200）：
```json
{
    "download_url": "https://bucket.oss-cn-hangzhou.aliyuncs.com/knowledge/1/2026-07/a1b2c3d4.pdf?Expires=1751234567&Signature=xxx..."
}
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
pm.test("含有效下载 URL", () => {
    const url = pm.response.json().download_url;
    pm.expect(url).to.include("https://");
    pm.expect(url).to.include("Expires=");
});
```

---

### 2.6 预览文档（文本 / 音视频）

| 项目 | 值 |
|------|-----|
| **接口名称** | 文档预览 |
| **请求方式** | `GET` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/preview/` |
| **权限** | 所有登录用户 |

**成功响应 — 文本类**（200）：
```json
{
    "preview_type": "text",
    "content": "软件工程是一门研究用工程化方法构建和维护软件的学科...",
    "file_type": "txt",
    "file_name": "课堂笔记.txt"
}
```

**成功响应 — 音频类**（200）：
```json
{
    "preview_type": "url",
    "content": "https://bucket.oss-cn-hangzhou.aliyuncs.com/...?Expires=...",
    "file_type": "audio",
    "file_name": "课件录音.mp3"
}
```

**成功响应 — 视频类**（200）：
```json
{
    "preview_type": "url",
    "content": "https://bucket.oss-cn-hangzhou.aliyuncs.com/...?Expires=...",
    "file_type": "video",
    "file_name": "实验操作演示.mp4"
}
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("包含 preview_type", () => pm.expect(json).to.have.property("preview_type"));
pm.test("包含 content", () => pm.expect(json.content).to.be.a("string").and.not.empty);
pm.test("包含 file_type", () => pm.expect(json).to.have.property("file_type"));
```

---

### 2.7 管理员录入文本

| 项目 | 值 |
|------|-----|
| **接口名称** | 管理员录入文本 |
| **请求方式** | `POST` |
| **路径** | `/api/knowledge/upload/text/` |
| **权限** | `IsAdminOrSuperAdmin` |

**Body → raw JSON**：
```json
{
    "title": "2026春季操作系统复习要点",
    "content": "一、进程管理\n1. 进程的状态转换：就绪→运行→阻塞\n2. 进程调度算法：FCFS、SJF、RR、优先级调度\n\n二、内存管理\n1. 分页存储管理\n2. 分段存储管理\n3. 虚拟内存技术\n\n三、文件系统\n1. 文件目录结构\n2. 磁盘调度算法",
    "description": "大三下学期操作系统课程重点整理",
    "college_id": 1,
    "discipline_id": 1,
    "category_id": 5,
    "keywords": ["操作系统", "进程管理", "内存管理", "期末考试"],
    "visibility": "public"
}
```

**字段说明**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | str | ✅ | 文档标题 |
| `content` | str | ✅ | 文本正文 |
| `description` | str | 否 | 文档描述 |
| `college_id` | int | ✅ | 学院 ID |
| `discipline_id` | int | ✅ | 学科方向 ID |
| `category_id` | int | ✅ | **必须是二级分类 ID** |
| `keywords` | list | 否 | 关键词字符串数组 |
| `visibility` | str | 否 | `public`（公开）/ `private`（私密） |

**成功响应**（200）：
```json
{
    "id": 15,
    "status": "uploaded",
    "file_name": "2026春季操作系统复习要点.txt"
}
```

**错误场景** — category_id 传了一级分类（如 `1` 是"教学资源"）：
```json
{ "category_id": ["必须是二级分类，不能选择一级分类"] }
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("返回 id", () => pm.expect(json.id).to.be.a("number"));
pm.test("状态 uploaded", () => pm.expect(json.status).to.equal("uploaded"));
pm.test("文件名 .txt 结尾", () => pm.expect(json.file_name).to.match(/\.txt$/));
```

---

### 2.8 获取 STS 上传凭证（含 MD5 去重）

| 项目 | 值 |
|------|-----|
| **接口名称** | 获取 OSS 上传凭证 |
| **请求方式** | `POST` |
| **路径** | `/api/knowledge/upload/sts/` |
| **权限** | `IsAdminOrSuperAdmin` |

**Body → raw JSON**：
```json
{
    "file_name": "软件工程导论.pdf",
    "file_size": 2048000,
    "file_type": "pdf",
    "md5": "d41d8cd98f00b204e9800998ecf8427e"
}
```

**成功响应 — 新文件**（200）：
```json
{
    "oss_key": "knowledge/1/2026-07/a1b2c3d4e5f6.pdf",
    "upload_url": "https://bucket.oss-cn-hangzhou.aliyuncs.com/knowledge/1/...?Expires=...&Signature=...",
    "sts_credentials": null,
    "dev_mode": false,
    "duplicate": false
}
```

**成功响应 — MD5 重复**（200）：
```json
{
    "duplicate": true,
    "resource_id": 3
}
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
if (json.duplicate) {
    pm.test("重复文件返回 resource_id", () => pm.expect(json.resource_id).to.be.a("number"));
} else {
    pm.test("返回 oss_key", () => pm.expect(json.oss_key).to.be.a("string").and.not.empty);
    pm.test("返回 upload_url", () => pm.expect(json.upload_url).to.include("https://"));
}
```

---

## 三、文件上传模块（5 个接口）⚠️ form-data

> 文件上传和普通 JSON 请求不同，**Body 类型必须选 `form-data`**。
>
> Apifox 操作：Body 标签 → 选 `form-data` → 每行右边有下拉选 `Text` 或 `File`。

---

### 3.1 文件直传上传（推荐！直接用 Apifox 选文件）

| 项目 | 值 |
|------|-----|
| **接口名称** | 文件直传上传 |
| **请求方式** | `POST` |
| **路径** | `/api/knowledge/upload/file/` |
| **权限** | `IsAdminOrSuperAdmin` |

> **这是最简单的文件上传方式！** Apifox 选一个本地文件 → 后端帮你传到 OSS → 自动创建数据库记录。

**Body → `form-data`**（Apifox 操作：Body → `form-data`）：

| 参数 | 类型选择 | 必填 | 示例值 |
|------|---------|------|--------|
| `file` | **📁 File** | ✅ | 点击选择本地文件，如 `软件工程导论.pdf` |
| `title` | Text | ✅ | `软件工程导论` |
| `description` | Text | 否 | `计算机学院软件工程专业入门教材` |
| `college_id` | Text | ✅ | `1` |
| `discipline_id` | Text | ✅ | `1` |
| `category_id` | Text | ✅ | `5`（二级分类 ID，不能传一级） |
| `keywords` | Text | 否 | `["软件工程","SDLC","期末考试"]` |
| `visibility` | Text | 否 | `public` |

> ⚠️ **`file` 那行的右边下拉一定要选 `File`**（不是 Text），然后点"选择文件"从本地挑一个 PDF/Word/MP4 等。
>
> ⚠️ `keywords` 格式是 JSON 数组字符串：`["关键词1","关键词2"]`
>
> ⚠️ `category_id` 必须传二级分类 ID（如 `5`=教学课件），传一级会报 400。

**成功响应**（200）：
```json
{
    "id": 18,
    "status": "uploaded",
    "file_name": "软件工程导论.pdf"
}
```

**错误 — 文件类型不支持**（400）：
```json
{
    "file": ["不支持的文件类型 \".exe\"，支持的类型：pdf, doc, docx, ppt, pptx, ..."]
}
```

**错误 — 分类是一级**（400）：
```json
{
    "category_id": ["必须是二级分类，不能选择一级分类"]
}
```

**错误 — 文件大小超限**（400）：
```json
{
    "detail": "mp4 文件大小不能超过 500MB，当前大小 600MB"
}
```

**Apifox 断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("返回 id", () => pm.expect(json.id).to.be.a("number"));
pm.test("状态 uploaded", () => pm.expect(json.status).to.equal("uploaded"));
pm.test("返回文件名", () => pm.expect(json.file_name).to.be.a("string").and.not.empty);
```

**创建接口的 Apifox 步骤截图说明**：

1. 在"文件上传模块"文件夹右键 → **"新建接口"**
2. 名称填 `文件直传上传`
3. 请求方式选 `POST`
4. 路径填 `/api/knowledge/upload/file/`
5. Header 加 `Authorization: Bearer {{token}}`
6. **Body 标签 → 选 `form-data`**
7. 第一行：参数名 `file`，右边下拉选 **`File`**，点"选择文件"
8. 第二行：参数名 `title`，右边下拉 `Text`，值填文档标题
9. 第三行：参数名 `college_id`，右边下拉 `Text`，值填 `1`
10. 依次添加其余参数 → 点 **"发送"**

---

### 3.2 上传完成回调（OSS 直传后通知后端）

| 项目 | 值 |
|------|-----|
| **接口名称** | 上传完成回调 |
| **请求方式** | `POST` |
| **路径** | `/api/knowledge/upload/callback/` |
| **权限** | `IsAdminOrSuperAdmin` |

> **调用时机**：前端用 STS 凭证直传 OSS 成功后，调此接口在数据库创建记录。

**Body → `form-data`**（全部选 `Text`）：

| 参数 | 类型 | 必填 | 示例值 |
|------|------|------|--------|
| `oss_key` | Text | ✅ | `knowledge/1/2026-07/a1b2c3d4e5f6.pdf` |
| `title` | Text | ✅ | `软件工程导论` |
| `description` | Text | ✅ | `计算机学院软件工程专业入门教材` |
| `file_name` | Text | ✅ | `软件工程导论.pdf` |
| `file_size` | Text | ✅ | `2048000` |
| `file_type` | Text | ✅ | `pdf` |
| `md5` | Text | ✅ | `d41d8cd98f00b204e9800998ecf8427e` |
| `college_id` | Text | ✅ | `1` |
| `discipline_id` | Text | ✅ | `1` |
| `category_id` | Text | ✅ | `5` |
| `keywords` | Text | 否 | `["软件工程","SDLC","期末考试"]` |
| `visibility` | Text | 否 | `public` |

> ⚠️ `keywords` 格式是 JSON 数组字符串：`["关键词1","关键词2"]`

**成功响应**（200）：
```json
{
    "id": 16,
    "status": "uploaded"
}
```

**错误场景 — 文件类型不在白名单**（400）：
```json
{ "file_type": ["不支持的文件类型 \"exe\"，支持的类型：pdf, doc, docx, ppt, ..."] }
```

**错误场景 — 文件大小超限**（400）：
```json
{ "file_size": ["mp4 文件大小不能超过 500MB，当前大小 600MB"] }
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("返回文档 id", () => pm.expect(json.id).to.be.a("number"));
pm.test("状态 uploaded", () => pm.expect(json.status).to.equal("uploaded"));
```

---

### 3.2 模拟 form-data 文件上传（仅用于理解格式）

> 如果你的前端还没有直传 OSS，可以用下面方式模拟完整流程。
> 实际项目中，文件是前端直接用 STS 凭证 **PUT 到 OSS**，不经过后端。

**流程**：
```
① POST /api/knowledge/upload/sts/   → 拿 oss_key + upload_url
② PUT {upload_url}（前端直传 OSS）   → 文件存入 OSS
③ POST /api/knowledge/upload/callback/ → 通知后端"我传完了"，后端创建 DB 记录
```

**第②步 Apifox 模拟**（选做）：
1. 新建接口 → 请求方式 `PUT`
2. URL 填第①步返回的 `upload_url`
3. Body → `binary` → 选一个本地文件
4. 点发送
5. 成功返回空响应（OSS 直传通常无返回体）

**前置脚本**（自动拿 sts 结果）：
```javascript
// 第一步：调 STS 接口
const stsRes = pm.sendRequest({
    url: pm.environment.get("base_url") + "/api/knowledge/upload/sts/",
    method: "POST",
    header: { "Authorization": "Bearer " + pm.environment.get("token"), "Content-Type": "application/json" },
    body: { mode: "raw", raw: JSON.stringify({
        file_name: "test.pdf", file_size: 1024000, file_type: "pdf",
        md5: "d41d8cd98f00b204e9800998ecf8427e"
    })}
});
const stsData = stsRes.json();
pm.environment.set("upload_url", stsData.upload_url);
pm.environment.set("oss_key", stsData.oss_key);
```

---

### 3.3 接口对比速查

| 步骤 | 谁调 | 数据流向 | Body 格式 |
|------|------|----------|-----------|
| STS 凭证 | 前端 → 后端 | 后端签凭证返回 | JSON |
| OSS 直传 | 前端 → OSS | 文件直传阿里云 | binary |
| 上传回调 | 前端 → 后端 | 通知后端"传完了" | form-data |

---

## 四、关键词管理模块（4 个接口）

### 4.1 查看文档的关键词列表

| 项目 | 值 |
|------|-----|
| **接口名称** | 文档关键词列表 |
| **请求方式** | `GET` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/keywords/` |
| **权限** | 所有登录用户 |

> **前置操作**：先跑 2.1 接口拿一个文档 `id`，填入路径。

**成功响应**（200）：
```json
[
    {
        "id": 10,
        "phrase": "操作系统",
        "match_type": "fuzzy",
        "weight": 5,
        "created_by_name": "admin",
        "created_at": "2026-06-15T10:30:00Z"
    },
    {
        "id": 11,
        "phrase": "进程调度",
        "match_type": "exact",
        "weight": 8,
        "created_by_name": "admin",
        "created_at": "2026-06-15T10:31:00Z"
    }
]
```
> 按 `weight` 降序排列，权重高的排前面。

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
const json = pm.response.json();
pm.test("返回数组", () => pm.expect(json).to.be.an("array"));
if (json.length > 0) {
    pm.test("含 phrase", () => pm.expect(json[0]).to.have.property("phrase"));
    pm.test("含 weight", () => pm.expect(json[0]).to.have.property("weight"));
    pm.test("含 created_by_name", () => pm.expect(json[0]).to.have.property("created_by_name"));
}
```

---

### 4.2 添加关键词

| 项目 | 值 |
|------|-----|
| **接口名称** | 添加关键词 |
| **请求方式** | `POST` |
| **路径** | `/api/knowledge/docs/{{doc_id}}/keywords/create/` |
| **权限** | `IsAdminOrSuperAdmin` |

**Body → raw JSON**：
```json
{
    "phrase": "进程调度",
    "match_type": "exact",
    "weight": 8
}
```

**字段说明**：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `phrase` | str | ✅ | — | 关键词文本 |
| `match_type` | str | 否 | `fuzzy` | `exact`/`fuzzy`/`semantic` |
| `weight` | int | 否 | `1` | 权重 1–10 |

**成功响应**（201）：
```json
{
    "id": 12,
    "phrase": "进程调度",
    "match_type": "exact",
    "weight": 8,
    "created_by_name": "admin",
    "created_at": "2026-07-02T12:00:00Z"
}
```

**错误场景 — 重复关键词**（409）：
```json
{ "detail": "关键词 \"进程调度\" 已存在于此文档中", "code": "CONFLICT" }
```

**错误场景 — 越权操作他人文档**（403）：
```json
{ "detail": "只能给自己上传的文档添加关键词" }
```

**后置断言**：
```javascript
pm.test("状态码 201", () => pm.response.to.have.status(201));
const json = pm.response.json();
pm.test("含 id", () => pm.expect(json.id).to.be.a("number"));
pm.test("phrase 正确", () => pm.expect(json.phrase).to.equal("进程调度"));
pm.test("weight 正确", () => pm.expect(json.weight).to.equal(8));
```

---

### 4.3 更新关键词

> ⚠️ 此接口对应 `KeywordUpdateView`，如果视图尚未创建，需先在 views.py 中添加。

| 项目 | 值 |
|------|-----|
| **接口名称** | 更新关键词 |
| **请求方式** | `PUT` |
| **路径** | `/api/knowledge/keywords/{{keyword_id}}/` |
| **权限** | `IsAdminOrSuperAdmin` |

**Body → raw JSON**：
```json
{
    "phrase": "进程管理与调度",
    "match_type": "semantic",
    "weight": 10
}
```

**成功响应**（200）：返回更新后的关键词对象。

---

### 4.4 删除关键词

| 项目 | 值 |
|------|-----|
| **接口名称** | 删除关键词 |
| **请求方式** | `DELETE` |
| **路径** | `/api/knowledge/keywords/{{keyword_id}}/delete/` |
| **权限** | `IsAdminOrSuperAdmin`，仅限自己文档下的关键词 |

**成功响应**（200）：
```json
{ "detail": "删除成功" }
```

**错误场景 — 越权**（403）：
```json
{ "detail": "只能删除自己上传的文档下的关键词" }
```

**后置断言**：
```javascript
pm.test("状态码 200", () => pm.response.to.have.status(200));
pm.test("删除成功", () => pm.response.json().detail === "删除成功");
```

---

## 五、分类数据查询接口（配合测试用）

### 5.1 学院列表

| 项目 | 值 |
|------|-----|
| **请求方式** | `GET` |
| **路径** | `/api/colleges/` |
| **权限** | `IsAuthenticated` |

**响应示例**：
```json
[
    { "id": 1, "name": "计算机与软件学院", "code": "CS", "sort_order": 1 },
    { "id": 2, "name": "智能科学与工程学院", "code": "ISE", "sort_order": 2 }
]
```

### 5.2 学科方向列表

| 项目 | 值 |
|------|-----|
| **请求方式** | `GET` |
| **路径** | `/api/disciplines/` |
| **Query** | `?category=工学` 按门类筛选 |

### 5.3 资源分类列表（树形 + 图标继承）

| 项目 | 值 |
|------|-----|
| **请求方式** | `GET` |
| **路径** | `/api/categories/` |

**响应示例**：
```json
[
    {
        "id": 1,
        "name": "教学资源",
        "parent": null,
        "icon": "https://oss/icons/teaching.png",
        "sort_order": 1,
        "children": [
            {
                "id": 5,
                "name": "教学课件",
                "parent": 1,
                "icon": "https://oss/icons/teaching.png",
                "sort_order": 1,
                "children": []
            }
        ]
    }
]
```

---

## 六、Apifox 进阶技巧

### 6.1 批量测试（自动化跑全部接口）

1. 点左侧 **"自动化测试"** → **"新建测试用例"**
2. 命名 `知识库全模块回归测试`
3. 从接口列表拖接口到测试步骤区域，按依赖顺序排列：
   ```
   ① 登录（拿 token）
   ② 学院列表（GET /colleges/）
   ③ 分类列表（GET /categories/）
   ④ STS 凭证（POST /upload/sts/）
   ⑤ 文本录入（POST /upload/text/）
   ⑥ 文档列表（GET /docs/?days_back=0）
   ⑦ 详情（GET /docs/{id}/）
   ⑧ 添加关键词（POST /docs/{id}/keywords/）
   ⑨ 关键词列表（GET /docs/{id}/keywords/）
   ⑩ 删除关键词（DELETE /keywords/{id}/delete/）
   ⑪ 删除文档（DELETE /docs/{id}/delete/）
   ```
4. 每个步骤后加断言脚本（按上文给出的脚本粘贴）
5. 点 **"运行"** → 自动跑完全部接口并出报告

### 6.2 环境变量联动（自动传递 ID）

在接口的后置脚本中提取 id，供下一步使用：

```javascript
// 放在 POST /upload/text/ 的 后置操作
const json = pm.response.json();
pm.environment.set("doc_id", json.id);       // 之后的接口用 {{doc_id}}
```

```javascript
// 放在 POST 添加关键词 的 后置操作
const json = pm.response.json();
pm.environment.set("keyword_id", json.id);   // 删除关键词时用 {{keyword_id}}
```

### 6.3 一键导出分享

1. 项目内点 **"导出"** → 选 `Apifox` 格式
2. 生成 `.apifox.json` 文件
3. 团队成员导入即可用，环境变量和断言全部保留

---

## 附录：接口速查表

### 知识库文档

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `GET` | `/api/knowledge/docs/` | 登录用户 | 列表（分页+过滤） |
| `GET` | `/api/knowledge/docs/{id}/` | 登录用户 | 详情 |
| `PUT` | `/api/knowledge/docs/{id}/` | IsOwner | 编辑元数据 |
| `DELETE` | `/api/knowledge/docs/{id}/delete/` | IsOwner | 删除 |
| `GET` | `/api/knowledge/docs/{id}/download/` | IsOwner | 下载签名URL |
| `GET` | `/api/knowledge/docs/{id}/preview/` | 登录用户 | 预览 |

### 文件上传

| 方法 | 路径 | 权限 | Body |
|------|------|------|------|
| `POST` | `/api/knowledge/upload/file/` | Admin | **form-data（选文件）** |
| `POST` | `/api/knowledge/upload/text/` | Admin | JSON |
| `POST` | `/api/knowledge/upload/sts/` | Admin | JSON |
| `POST` | `/api/knowledge/upload/callback/` | Admin | form-data |

### 关键词

| 方法 | 路径 | 权限 |
|------|------|------|
| `GET` | `/api/knowledge/docs/{doc_id}/keywords/` | 登录用户 |
| `POST` | `/api/knowledge/docs/{doc_id}/keywords/create/` | Admin |
| `PUT` | `/api/knowledge/keywords/{id}/` | Admin |
| `DELETE` | `/api/knowledge/keywords/{id}/delete/` | Admin |
