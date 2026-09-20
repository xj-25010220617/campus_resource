# 知识库「一劳永逸」可靠性改造 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知识库的检索、上传、存储三条链路可靠运转，问答精准且严谨、上传完整、存储完整准确。

**Architecture:** 向量库统一到 Milvus（移除 Chroma），Embedding 用本地 bge-base-zh-v1.5（768 维），OCR 用 PaddleOCR 为主，LLM 通过 Provider 抽象可插拔（当前云 DeepSeek，后期本地），按角色分严谨度，字符集迁 utf8mb4，最后全量重索引。

**Tech Stack:** Django 4.2 + MySQL 8.0 + Milvus（pymilvus）+ sentence-transformers（bge-base-zh-v1.5）+ PaddleOCR + easyocr + Redis/Celery。

**Spec:** [docs/superpowers/specs/2026-09-21-knowledge-base-reliability-design.md](../specs/2026-09-21-knowledge-base-reliability-design.md)

## Global Constraints

- 向量维度统一为 **768**（bge-base-zh-v1.5）
- 向量库唯一事实源：**Milvus**（移除 Chroma）
- Embedding 后端：`EMBED_BACKEND=local`，模型 `BAAI/bge-base-zh-v1.5`
- OCR：PaddleOCR 为主、easyocr 兜底；移除云端 qwen-vl 优先
- 问答 LLM：当前 `.env` 实际配的是 **DeepSeek**（`DEEPSEEK_BASE_URL=api.deepseek.com`，经 `QWEN_*` 变量回退兼容），Provider 抽象必须兼容 OpenAI 兼容接口
- 字符集：`utf8mb4`，移除 4 字节字符清洗
- 测试命令：`python manage.py test <app> --settings=config.settings_test`（已存在 `config/settings_test.py` + `config/sqlite_test_backend`）
- 分角色严谨度：管理员（super_admin/admin/college_admin/dept_admin）强制引用 + 低置信拒答；学生（user）灵活

---

### Task 1: 依赖管理 — 加本地 AI 依赖、移除 Chroma

**Files:**
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: 后续 Task 2/3/4 所需的 `pymilvus`、`sentence-transformers`、`paddleocr`、`paddlepaddle`、`easyocr` 已就位；`chromadb`/`chroma-hnswlib` 已移除

- [ ] **Step 1: 移除 Chroma 依赖**

在 `requirements.txt` 中删除以下三行（含注释行）：

```
# 本地向量数据库（开发环境 Milvus 降级方案）
# - 固定 0.6.3：chromadb 1.5.x 的 Rust hnsw 在 Windows 上新进程读取崩溃；
#   0.6.3 需搭配 chroma-hnswlib==0.7.3（0.7.6 写出的索引互不兼容）
chromadb==0.6.3
chroma-hnswlib==0.7.3
```

- [ ] **Step 2: 添加本地 AI 依赖**

在 `requirements.txt` 末尾追加：

```
# Milvus 向量库客户端
pymilvus>=2.4.0
# 本地 Embedding（bge-base-zh-v1.5）
sentence-transformers>=2.7.0
# 本地 OCR（中文扫描件识别，主）
paddlepaddle>=2.6.0
paddleocr>=2.7.0
# 本地 OCR（兜底）
easyocr>=1.7.0
```

- [ ] **Step 3: 提交**

```bash
git add backend/requirements.txt
git commit -m "chore: 移除 Chroma，添加 Milvus/本地 Embedding/OCR 依赖"
```

---

### Task 2: Milvus 接通 — 懒连接 + 移除 Chroma + 维度 768

**Files:**
- Modify: `backend/utils/milvus.py`
- Test: `backend/apps/knowledge/tests/test_milvus_client.py`

**Interfaces:**
- Consumes: Task 1 的 `pymilvus`
- Produces: `MilvusClient.connect()` 幂等；`_connected` 首次向量操作自动变 True；删除 `_*_chroma` 方法；`VECTOR_DIM=768`

- [ ] **Step 1: 写失败测试（connect 幂等 + 懒连接）**

创建 `backend/apps/knowledge/tests/__init__.py` 和 `backend/apps/knowledge/tests/test_milvus_client.py`：

```python
from unittest import mock
from django.test import SimpleTestCase


class TestMilvusLazyConnect(SimpleTestCase):
    def test_connect_idempotent(self):
        from utils.milvus import MilvusClient
        c = MilvusClient()
        assert c.connected is False
        # 模拟 pymilvus 连接成功
        with mock.patch('pymilvus.connections.connect', return_value=None):
            assert c.connect() is True
            assert c.connected is True
            # 再次调用不再重复 connect
            with mock.patch('pymilvus.connections.connect') as m:
                assert c.connect() is True
                m.assert_not_called()

    def test_lazy_connect_on_search(self):
        from utils.milvus import MilvusClient
        c = MilvusClient()
        with mock.patch.object(c, 'connect', return_value=True) as mc:
            with mock.patch.object(c, '_search_milvus', return_value=[]) as ms:
                c.search([0.1] * 768, top_k=3)
                mc.assert_called_once()
                ms.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && python manage.py test apps.knowledge.tests.test_milvus_client --settings=config.settings_test -v 2
```

预期 FAIL：`MilvusClient` 无懒连接逻辑 / `connect` 不幂等。

- [ ] **Step 3: 改 `utils/milvus.py` 实现懒连接 + 幂等 + 维度**

- `VECTOR_DIM = 1024` → `VECTOR_DIM = 768`
- `connect()` 顶部加幂等保护：

```python
def connect(self) -> bool:
    if self._connected:
        return True
    try:
        from pymilvus import connections
        connections.connect(
            alias='default',
            host=getattr(settings, 'MILVUS_HOST', 'localhost'),
            port=getattr(settings, 'MILVUS_PORT', '19530'),
        )
        self._connected = True
        logger.info('Milvus connected')
        return True
    except ImportError:
        logger.warning('pymilvus not installed')
        self._connected = False
        return False
    except Exception as e:
        logger.warning(f'Milvus connection failed: {e}')
        self._connected = False
        return False
```

- 每个向量操作入口加懒连接（`ensure_collection` / `insert_chunks` / `search` / `delete_by_doc`），例如 `search`：

```python
def search(self, query_embedding, top_k=10, filter_ids=None):
    if not self._connected:
        self.connect()
    return self._search_milvus(query_embedding, top_k, filter_ids)
```

- 删除 Chroma 分支：`_ensure_chroma_collection`、`_insert_chroma`、`_search_chroma`、`_brute_force_chroma`、`delete_by_doc` 里的 `try: import chromadb ...` 整块；`ensure_collection` / `insert_chunks` / `search` / `delete_by_doc` 直接调用 `_*_milvus` 版本

- `delete_by_doc` 改为只走 Milvus：

```python
def delete_by_doc(self, doc_id: int) -> bool:
    if not self._connected:
        self.connect()
    if not self._connected:
        return False
    from pymilvus import Collection
    collection = Collection(name=self.COLLECTION_NAME)
    collection.delete(f'doc_id == {doc_id}')
    return True
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && python manage.py test apps.knowledge.tests.test_milvus_client --settings=config.settings_test -v 2
```

预期 PASS。

- [ ] **Step 5: 语法/导入检查**

```bash
cd backend && python -m py_compile utils/milvus.py && echo OK
```

- [ ] **Step 6: 提交**

```bash
git add backend/utils/milvus.py backend/apps/knowledge/tests/
git commit -m "feat: Milvus 懒连接 + 移除 Chroma + 维度统一 768"
```

---

### Task 3: 本地 Embedding — bge-base-zh-v1.5 + query 前缀 + 维度对齐

**Files:**
- Modify: `backend/utils/llm.py`
- Modify: `backend/config/settings.py`
- Modify: `backend/utils/rag.py`（`_cached_embed` query 侧加前缀）
- Test: `backend/apps/knowledge/tests/test_embedding.py`

**Interfaces:**
- Consumes: Task 1 的 `sentence-transformers`
- Produces: `llm_client.embed(texts)` 返回 768 维；`_embed_via_local` 支持 query/passage 区分；`LLMClient.VECTOR_DIM=768`

- [ ] **Step 1: 改 `config/settings.py` 默认值**

- `LOCAL_EMBED_MODEL` 默认从 `paraphrase-multilingual-MiniLM-L12-v2` 改为 `BAAI/bge-base-zh-v1.5`
- `EMBED_BACKEND` 默认从 `aliyun` 改为 `local`（第 197 行）

- [ ] **Step 2: 改 `utils/llm.py` 维度 + bge 前缀**

- `LLMClient.VECTOR_DIM` 1024 → 768（类属性，`_mock_embed` 里 `range(LLMClient.VECTOR_DIM)` 会自动跟随）
- `_embed_via_local` 改为支持 query 前缀：

```python
BGE_QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'

def _embed_via_local(self, texts, is_query=False):
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(LOCAL_EMBED_MODEL)
        if is_query:
            texts = [BGE_QUERY_PREFIX + t for t in texts]
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()
    except ImportError:
        logger.warning('sentence-transformers 未安装')
        return None
    except Exception as e:
        logger.error(f'本地 Embedding 失败: {e}')
        return None
```

- `embed()` 增加可选参数 `is_query=False` 透传给本地后端；aliyun 后端忽略该参数

- [ ] **Step 3: 改 `utils/rag.py` 的 `_cached_embed` query 侧加前缀**

定位 `_cached_embed`（用于 query embedding），改为调用 `llm_client.embed([q], is_query=True)`。索引侧（`apps/knowledge/tasks.py` 的 `llm_client.embed(texts)`）保持 `is_query=False`。

- [ ] **Step 4: 写测试（维度 + 前缀）**

创建 `backend/apps/knowledge/tests/test_embedding.py`：

```python
from unittest import mock
from django.test import SimpleTestCase


class TestEmbedding(SimpleTestCase):
    def test_vector_dim_768(self):
        from utils.llm import LLMClient
        assert LLMClient.VECTOR_DIM == 768

    def test_query_prefix_added(self):
        from utils.llm import LLMClient, BGE_QUERY_PREFIX
        fake = mock.MagicMock()
        fake.encode.return_value.__getitem__ = lambda *a: [[0.0] * 768]
        import numpy as np
        fake.encode.return_value = np.zeros((2, 768))
        with mock.patch('sentence_transformers.SentenceTransformer', return_value=fake):
            client = LLMClient()
            client._embed_via_local(['校园网怎么登录'], is_query=True)
            arg = fake.encode.call_args[0][0]
            assert arg[0].startswith(BGE_QUERY_PREFIX)
```

- [ ] **Step 5: 跑测试**

```bash
cd backend && python manage.py test apps.knowledge.tests.test_embedding --settings=config.settings_test -v 2
```

预期 PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/utils/llm.py backend/config/settings.py backend/utils/rag.py backend/apps/knowledge/tests/
git commit -m "feat: 本地 Embedding 切 bge-base-zh-v1.5 + query 前缀 + 维度 768"
```

---

### Task 4: OCR 本地化 — PaddleOCR 为主 + easyocr 兜底

**Files:**
- Modify: `backend/apps/knowledge/extractor.py`

**Interfaces:**
- Consumes: Task 1 的 `paddleocr`/`easyocr`
- Produces: `_ocr_image` 走 PaddleOCR→easyocr，移除云 qwen-vl 优先

- [ ] **Step 1: 重写 `_ocr_image` 与相关懒加载**

在 `extractor.py` 中新增 PaddleOCR 懒加载单例，并将 `_ocr_image` 改为本地优先：

```python
_paddle_ocr = None
_paddle_ocr_failed = False

def _get_paddle_ocr():
    global _paddle_ocr, _paddle_ocr_failed
    if _paddle_ocr is None and not _paddle_ocr_failed:
        try:
            from paddleocr import PaddleOCR
            _paddle_ocr = PaddleOCR(use_angle_cls=True, lang='ch')
        except Exception as e:
            logger.error(f'PaddleOCR 初始化失败: {e}')
            _paddle_ocr_failed = True
    return _paddle_ocr


def _ocr_image_paddle(image_bytes: bytes) -> str:
    import numpy as np
    from PIL import Image
    ocr = _get_paddle_ocr()
    if ocr is None:
        return ''
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        arr = np.asarray(img)
        result = ocr.ocr(arr, cls=True)
        lines = []
        for page in (result or []):
            if not page:
                continue
            for line in page:
                txt = line[1][0] if len(line) > 1 else ''
                if txt:
                    lines.append(txt)
        return '\n'.join(lines).strip()
    except Exception as e:
        logger.warning(f'PaddleOCR 识别失败: {e}')
        return ''


def _ocr_image(image_bytes: bytes) -> str:
    text = _ocr_image_paddle(image_bytes)
    if text:
        return text
    logger.info('PaddleOCR 返回空，回退 easyocr')
    return _ocr_image_local(image_bytes)
```

（`_ocr_image_local` / `_get_easyocr_reader` 已在文件中，保持不变；删除或跳过 `_ocr_image_cloud` 在 `_ocr_image` 里的优先调用，保留函数体但不再作为默认路径）

- [ ] **Step 2: 语法/导入检查**

```bash
cd backend && python -m py_compile apps/knowledge/extractor.py && echo OK
```

- [ ] **Step 3: 提交**

```bash
git add backend/apps/knowledge/extractor.py
git commit -m "feat: OCR 改为 PaddleOCR 为主、easyocr 兜底"
```

---

### Task 5: 存储 — utf8mb4 迁移 + 移除 4 字节清洗

**Files:**
- Create: `backend/apps/knowledge/migrations/00XX_utf8mb4.py`（数据迁移，动态序号）
- Modify: `backend/apps/knowledge/tasks.py`

**Interfaces:**
- Consumes: 远程 MySQL 8.0（`DB_HOST=8.156.65.242`）
- Produces: 库/表字符集 `utf8mb4`；`_cleanse_for_mysql_utf8mb3` 不再被调用

- [ ] **Step 1: 生成数据迁移切换字符集**

```bash
cd backend && python manage.py makemigrations knowledge --empty -n utf8mb4_charset
```

编辑生成的迁移文件，`RunSQL` 切换库与相关表：

```python
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('knowledge', '<上一个迁移名>'),
    ]
    operations = [
        migrations.RunSQL(
            sql="ALTER DATABASE campus_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            reverse_sql="ALTER DATABASE campus_db CHARACTER SET utf8 COLLATE utf8_unicode_ci;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE knowledge_doc_chunk CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            reverse_sql="ALTER TABLE knowledge_doc_chunk CONVERT TO CHARACTER SET utf8 COLLATE utf8_unicode_ci;",
        ),
    ]
```

（表名以实际 `DocChunk` 对应表 `knowledge_doc_chunk` 为准；`knowledge_doc`、`keyword` 若含文本列也一并转换）

- [ ] **Step 2: 移除 4 字节清洗调用**

在 `apps/knowledge/tasks.py` 中，把 `DocChunk(content=_cleanse_for_mysql_utf8mb3(c['content']), ...)` 改回 `content=c['content']`，并删除 `_cleanse_for_mysql_utf8mb3` 函数体（或保留但不再调用）。

- [ ] **Step 3: 语法检查**

```bash
cd backend && python -m py_compile apps/knowledge/tasks.py && echo OK
```

- [ ] **Step 4: 提交**

```bash
git add backend/apps/knowledge/migrations/ backend/apps/knowledge/tasks.py
git commit -m "feat: 字符集迁移 utf8mb4，移除 4 字节清洗"
```

---

### Task 6: LLM Provider 可插拔

**Files:**
- Create: `backend/utils/llm_provider.py`
- Modify: `backend/utils/llm.py`
- Modify: `backend/config/settings.py`

**Interfaces:**
- Consumes: 现有 `llm_client.chat / chat_stream / chat_vision` 的调用方（`apps/chat/views.py`、`extractor.py` 等）
- Produces: `LLM_PROVIDER=qwen|local` 配置；`llm_client` 三个 chat 接口行为不变，内部按 provider 分发

- [ ] **Step 1: 新增 `utils/llm_provider.py` 抽象**

```python
class LLMProvider:
    def chat(self, messages, **kw): raise NotImplementedError
    def chat_stream(self, messages, **kw): raise NotImplementedError
    def chat_vision(self, prompt, images, **kw): raise NotImplementedError
```

- [ ] **Step 2: 将现有云实现抽成 `QwenProvider`**

把 `utils/llm.py` 里 `chat` / `chat_stream` / `chat_vision` 的现有逻辑（OpenAI 兼容，当前实际走 DeepSeek）移入 `QwenProvider(LLMProvider)`，`llm_client` 保留同名方法委托给 provider 单例。

- [ ] **Step 3: 加配置切换**

`settings.py` 加 `LLM_PROVIDER = config('LLM_PROVIDER', default='qwen')`；`llm.py` 末尾按 `LLM_PROVIDER` 实例化 provider（`local` 分支暂留 `LocalProvider` 占位，抛 `NotImplementedError` 提示后期接入）。

- [ ] **Step 4: 语法/导入检查 + 冒烟**

```bash
cd backend && python -m py_compile utils/llm.py utils/llm_provider.py && echo OK
```

- [ ] **Step 5: 提交**

```bash
git add backend/utils/llm.py backend/utils/llm_provider.py backend/config/settings.py
git commit -m "feat: LLM Provider 抽象，支持云/本地切换"
```

---

### Task 7: 分角色严谨度 — 管理员拒答 + 强制引用

**Files:**
- Modify: `backend/utils/rag.py`
- Modify: `backend/apps/chat/views.py`

**Interfaces:**
- Consumes: `retrieve(question, user_context, ...)` 已返回带 `score` 的命中列表
- Produces: 新增 `should_refuse(role, top_score)` / 检索结果带角色判断；管理员答案强制附 `references`

- [ ] **Step 1: 在 `utils/rag.py` 加拒答阈值常量与判断**

```python
ADMIN_MIN_SCORE = 0.5  # 管理员低置信阈值（可调）
ADMIN_ROLES = {'super_admin', 'admin', 'college_admin', 'dept_admin'}

def is_strict_role(role: str) -> bool:
    return role in ADMIN_ROLES
```

- [ ] **Step 2: 在 `apps/chat/views.py` 生成前加拒答分支**

定位 `_stream_answer` / `generate`（`rag.retrieve` 调用后、`llm_client.chat_stream` 前），对管理员角色：若 `retrieve` 返回空或 top score < `ADMIN_MIN_SCORE`，直接流式返回「知识库暂无相关资料，无法给出可靠回答」，不调用 LLM。

- [ ] **Step 3: 管理员强制附引用**

管理员路径生成答案后，确保 `references` 非空（由检索命中填充）；学生路径不强制。

- [ ] **Step 4: 写测试（角色判断）**

创建 `backend/apps/knowledge/tests/test_rag_role.py`：

```python
from django.test import SimpleTestCase
from utils.rag import is_strict_role, ADMIN_ROLES


class TestRoleStrict(SimpleTestCase):
    def test_admin_is_strict(self):
        for r in ADMIN_ROLES:
            assert is_strict_role(r) is True

    def test_user_not_strict(self):
        assert is_strict_role('user') is False
```

- [ ] **Step 5: 跑测试 + 提交**

```bash
cd backend && python manage.py test apps.knowledge.tests.test_rag_role --settings=config.settings_test -v 2
git add backend/utils/rag.py backend/apps/chat/views.py backend/apps/knowledge/tests/
git commit -m "feat: 分角色严谨度，管理员低置信拒答 + 强制引用"
```

---

### Task 8: 全量重索引 + 质量监控

**Files:**
- Create: `backend/scripts/reindex_all.py`
- Modify: `backend/utils/rag.py`（监控日志增强）

**Interfaces:**
- Consumes: Task 2/3/4 的 Milvus + 本地 Embedding + OCR
- Produces: 一键重索引脚本；监控日志补索引成功率/向量命中率

- [ ] **Step 1: 写 `scripts/reindex_all.py`**

```python
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django
django.setup()

from django.db.models import Q
from apps.knowledge.models import KnowledgeDoc
from apps.knowledge.tasks import index_document_task
from utils.milvus import milvus_client

def main():
    milvus_client.connect()
    if not milvus_client.connected:
        print('Milvus 未连接，请先启动 Milvus (19530)')
        return
    # 重建 collection（drop + 重建，清空孤儿向量）
    from pymilvus import Collection, utility
    if utility.has_collection(milvus_client.COLLECTION_NAME):
        utility.drop_collection(milvus_client.COLLECTION_NAME)
    milvus_client.ensure_collection()
    # 全量重索引（含 failed/embedding/indexed）
    docs = KnowledgeDoc.objects.filter(~Q(status='uploading'))
    for i, doc in enumerate(docs):
        print(f'[{i+1}/{docs.count()}] reindex doc_id={doc.id}')
        index_document_task(doc.id)
    print('done')

if __name__ == '__main__':
    main()
```

- [ ] **Step 2: 增强 `utils/rag.py` 监控日志**

在现有 `[RAG监控]` 日志处补 `向量命中率 = len(vector_hits)/len(candidate_ids)`、`索引状态分布`（可选），保持轻量。

- [ ] **Step 3: 语法检查 + 提交**

```bash
cd backend && python -m py_compile scripts/reindex_all.py utils/rag.py && echo OK
git add backend/scripts/reindex_all.py backend/utils/rag.py
git commit -m "feat: 全量重索引脚本 + 检索质量监控增强"
```

---

## Self-Review 结果

- **Spec 覆盖**：模块 1→Task 2、模块 2→Task 3、模块 3→Task 4、模块 4→Task 6、模块 5→Task 7、模块 6→Task 5、模块 7→Task 8、模块 8→Task 8，全部覆盖。
- **Placeholder**：Task 5 的迁移依赖项 `<上一个迁移名>` 与表名需实施时按实际补全（已注明"以实际为准"）。
- **类型一致**：`VECTOR_DIM=768` 在 Task 2/3 一致；`_embed_via_local(texts, is_query=False)` 签名 Task 3 定义、Task 3 Step 3 调用一致；`is_strict_role` 在 Task 7 定义并使用一致。

## 执行说明

- Task 1 依赖安装、Task 5 数据库迁移、Task 8 重索引需访问真实环境（远程 MySQL `8.156.65.242`、Milvus `19530`、首次模型下载），执行时需确认这些服务可用。
- 本计划按依赖顺序执行；Task 2/3 合并验证「上传→索引→检索」闭环后再进入后续 Task 更稳妥。
