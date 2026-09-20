# 知识库「一劳永逸」可靠性改造设计

> 日期：2026-09-21
> 状态：待 review
> 范围：检索、上传、存储三条链路的系统性改造

## 1. 背景与目标

校园虚拟资源管理系统的知识库（RAG 问答）当前存在系统性问题：上传的文档无法通过内容关键词被检索命中，甚至拿不到源文件。用户诉求三个质量目标，要求一次根治、不留后患：

1. **问答精准且严谨** — 检索命中准确，答案忠实于文档、可溯源
2. **上传完整** — 任意格式文档都能完整处理，不丢内容
3. **存储完整准确** — 内容不截断、不丢字符、向量不丢失、源文件可完整取回

## 2. 约束

| 约束 | 决策 |
|------|------|
| 部署环境 | 学校服务器，Milvus + Redis + Celery 完整中间件可用（真实生产用高配服务器，不设降级） |
| Embedding | 本地 `bge-base-zh-v1.5`（768 维），中文语义精准 |
| OCR | 本地 `PaddleOCR`（PP-OCRv4/v5）为主，`easyocr` 兜底 |
| 问答 LLM | 现在用云 qwen-flash，`LLMProvider` 抽象接口，后期切本地模型 |
| 严谨度 | 分角色：管理员强制引用 + 低置信拒答；学生端灵活 |
| 存储 | `utf8mb4`，不丢 4 字节字符；源文件 OSS 原文保存 |

## 3. 现状问题（根因）

### 3.1 向量检索完全失效（检索不精准的主因）

- `MilvusClient._connected` 初始为 `False`，唯一能翻 `True` 的 `connect()` 只在死代码（`config/celery.py` 旧 task）和手动脚本里调用
- 运行时路径（`utils/rag.py` 的 `retrieve()`、`apps/knowledge/tasks.py` 的 `index_document_task`、`apps/knowledge/views.py` 的删除）直接调 `search/insert/delete`，从不先 `connect()`
- 因此所有向量读写落到已损坏的 Chroma 分支：中文路径导致 chroma-hnswlib C++ 层崩溃 + chromadb 1.5.x Rust hnsw 崩溃
- 历史 Chroma 库 3191 条向量中 3182 条是孤儿，22 篇有效文档 17 篇向量丢失

### 3.2 索引失败导致文档不可检索（上传不完整）

- 扫描件（无文字层）PDF 抽取文本为空 → `index_document_task` 判 `failed`
- 所有搜索通道都硬性要求 `status='indexed'`，失败文档即使标题/文件名/关键词已入库也完全不可见
- 脏数据：47 篇 `failed`、8 篇 `embedding` 卡死、33 篇 `indexed`（但多数向量已丢）

### 3.3 存储不准确

- `utf8mb3` 字符集丢 4 字节字符（emoji 等），靠 `_cleanse_for_mysql_utf8mb3` 清洗兜底
- 向量维度混乱：`VECTOR_DIM=1024`（对应云 text-embedding-v3），但本地模型是 384 维，切本地后维度不匹配

### 3.4 检索质量问题（部分已修）

- 同文档多 chunk 重复展示、分数无限叠加（已由 `best_by_doc` 部分修复）
- 无拒答机制：知识库没有时会硬凑答案
- 引用溯源不强制

## 4. 架构决策

| 决策点 | 方案 |
|--------|------|
| 向量库 | Milvus 唯一事实源，**彻底移除 Chroma** 分支 |
| Embedding | 本地 `BAAI/bge-base-zh-v1.5`（768 维），query 需加 bge 检索前缀 |
| OCR | `PaddleOCR` 为主，`easyocr` 兜底；移除云端 qwen-vl 优先 |
| LLM | `LLMProvider` 抽象（chat / chat_stream / chat_vision），云 qwen 与本地模型两个实现 |
| 严谨度 | 检索结果携带 `role`，管理员路径启用拒答 + 强制引用 |
| 字符集 | MySQL `utf8mb4`，移除 4 字节清洗 |
| 数据迁移 | 清空 Milvus collection → 全量重索引 |

## 5. 模块设计

### 模块 1 — Milvus 接通（检索精准的根基）

**改动文件**：`utils/milvus.py`

- `MilvusClient` 加懒连接：`connect()` 幂等（已连接则直接返回），失败后下次调用重试；`_connected` 从「恒 False」变为「首次向量操作自动连接」
- 删除 `_*_chroma` 全部分支（`_ensure_chroma_collection` / `_insert_chroma` / `_search_chroma` / `_brute_force_chroma` / `delete_by_doc` 的 Chroma 分支），向量只走 Milvus
- `VECTOR_DIM` 1024 → 768，Milvus collection 按 768 维重建
- `CHROMA_DB_PATH` 等 Chroma 配置一并移除

### 模块 2 — 本地 Embedding 接入

**改动文件**：`utils/llm.py`、`config/settings.py`

- `EMBED_BACKEND=local`，`LOCAL_EMBED_MODEL=BAAI/bge-base-zh-v1.5`
- `LLMClient.VECTOR_DIM` 1024 → 768
- `_mock_embed` 同步改 768 维（仅开发降级用）
- bge 模型 query 侧加检索前缀 `为这个句子生成表示以用于检索相关文章：`，passage 侧不加（bge-v1.5 的要求）；`_cached_embed` 需区分 query/passage

### 模块 3 — OCR 本地化

**改动文件**：`apps/knowledge/extractor.py`、`requirements.txt`

- `_ocr_image` 改为 `PaddleOCR` 为主、`easyocr` 兜底，移除云端 qwen-vl 优先（保留 `QWEN_VL_MODEL` 配置项作可选开关，默认关闭）
- PaddleOCR 懒加载单例（首次 OCR 才初始化），`use_angle_cls=True`、`lang='ch'`，具体调用按安装版本适配
- 保留 `OCR_MAX_PAGES` 页数上限，避免超长扫描件失控

### 模块 4 — LLM Provider 可插拔

**改动文件**：`utils/llm.py`（或新增 `utils/llm_provider.py`）

- 抽象 `chat / chat_stream / chat_vision` 三个接口
- `QwenProvider`（云，现有实现）+ `LocalProvider`（本地模型，后期实现，接口预留）
- 配置项 `LLM_PROVIDER=qwen|local` 切换，后期本地模型就绪后改一行配置即可
- 现有 `llm_client.chat*` 调用方无需改动

### 模块 5 — 分角色严谨度

**改动文件**：`utils/rag.py`、`apps/chat/views.py`

- 检索结果携带 `role`（来自 `user_context`）
- 管理员（`super_admin`/`admin`/`college_admin`/`dept_admin`）路径：
  - 低置信度（合并后 score 低于阈值）不硬凑 → 返回「知识库暂无相关资料」
  - 答案强制附引用来源（`references` 必填）
- 学生（`user`）路径：不强制，保持灵活

### 模块 6 — 存储完整准确

**改动文件**：数据库迁移、`apps/knowledge/tasks.py`

- 生成 `utf8mb4` 迁移（库、表、连接字符集）
- 移除 `_cleanse_for_mysql_utf8mb3` 清洗，4 字节字符（emoji）原样保留
- 分块保持 500 字滑动窗口（100 重叠），不引入额外截断；源文件 OSS 原文保存、可完整下载

### 模块 7 — 历史数据根治（一次性）

**改动文件**：新增 `scripts/reindex_all.py`

- 清空 Milvus collection（drop + 重建）
- 遍历全部 `KnowledgeDoc`（含 `failed`/`embedding`/`indexed` 全部状态），重新走「抽取 → OCR → 分块 → 本地 Embedding → 写入 Milvus + MySQL」
- 重建后验证：`DocChunk` 数量、Milvus 向量数量、`status` 全部收敛为 `indexed` 或明确失败原因

### 模块 8 — 质量监控（防复发）

**改动文件**：`utils/rag.py`（现有监控日志增强）

- 保留现有 `[RAG监控]` 日志，补：索引成功率、向量命中率、零召回告警
- 目标：学校投入后能第一时间发现「向量通道失效」这类问题，而非事后排查

## 6. 实施顺序

1. 模块 1 + 2（Milvus 接通 + 本地 Embedding，维度对齐 768）
2. 模块 3（OCR 本地化）
3. 模块 6（utf8mb4 迁移）
4. 模块 4（LLM Provider 可插拔）
5. 模块 5（分角色严谨度）
6. 模块 7（全量重索引）
7. 模块 8（监控）

## 7. 测试策略

- 单元测试：`connect()` 幂等、维度对齐、bge 前缀、OCR 兜底链
- 集成测试：上传文本 PDF / 扫描件 PDF / DOCX → 索引成功 → 内容关键词能检索命中并返回源文件引用
- 检索质量测试：同文档多块不重复、分数不虚高、管理员低置信拒答
- 数据迁移测试：全量重索引后 `status` 收敛、向量数量与 chunk 数量一致

## 8. 风险与回滚

| 风险 | 缓解 |
|------|------|
| Milvus 部署失败 | 懒连接失败自动重试；监控日志记录连接状态 |
| PaddleOCR 安装/依赖重 | 提供 easyocr 兜底；requirements 固定版本 |
| utf8mb4 迁移影响现有数据 | 迁移前备份；迁移脚本可回滚 |
| 本地模型首次下载耗时 | 部署时预下载模型；文档说明 |
