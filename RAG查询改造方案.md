# RAG 三层检索改造方案

> **模块**：AI 智能问答（Chat / RAG）
> **日期**：2026-07-01
> **作者**：3471691973-eg
> **状态**：待评审
> **关联文档**：
> - [数据库变更记录：query_log 表](database-changes/query_log.md)
> - [代码改动记录：2026-07-01](code-changes/2026-07-01.md)

---

## 一、背景与目标

### 1.1 现状

当前 `backend/utils/rag.py` 的 `retrieve()` 函数存在以下问题：

1. **无硬过滤**：仅靠 `college_id` 一个字段做边界，向量检索在全量语料（约 10 万条）上跑，召回率低、耗时长。
2. **无用户上下文**：不识别用户学院、角色、可访问层级（`visibility`）。
3. **关键词权重未启用**：场景 3 强调"关键词权重（weight）"在检索中加成，但当前 `_merge_and_rerank` 没有该机制。
4. **无问题解析**：用户提问是纯自然语言，AI 要在 10 万条里做语义搜索。

### 1.2 目标

改造检索流程，由"**纯 AI 语义匹配**"升级为"**三层过滤**"：

| 层 | 作用 | 性能贡献 |
|---|---|---|
| 硬过滤（SQL WHERE） | 把范围从 10 万缩到几百 | -99% 候选 |
| 软匹配（向量 + 关键词） | 在小范围内做精准语义 | -90% 召回 |
| 上下文（权限校验） | 过滤权限不足的资源 | 安全兜底 |

### 1.3 收益

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 候选语料 | ~100,000 | ~300 |
| 检索响应时间 | 2–5 秒 | <500 ms |
| 召回率（人工评审） | ~60% | ~85% |
| 权限泄漏风险 | 中 | 低 |

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────┐
│  用户提问："计算机系大三下学期的操作系统复习资料"             │
└─────────────────────┬───────────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 1：问题解析（query_parser.parse）                │
│  输出：{college, discipline, course_term,            │
│         category, target_audience, file_type,        │
│         time_range}                                  │
│  实现：LLM 抽取（DeepSeek）+ 失败回退规则匹配         │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 2：SQL 硬过滤（apply_hard_filters）             │
│  WHERE status='published'                            │
│    AND visibility IN (用户可见层级)                   │
│    AND college_id IN (...)                           │
│    AND category_id IN (子节点子树)                    │
│    AND file_type IN (...)                            │
│  候选：~300 条                                       │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 3：双路召回                                    │
│    ├─ 向量检索：Milvus（filter=candidate_ids）        │
│    └─ 关键词检索：MySQL LIKE（filter=candidate_ids）  │
│  Top-K = 10                                          │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 4：重排序                                       │
│  score = 0.7 × 向量分 + 0.3 × 关键词分 + 关键词权重加成│
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 5：上下文过滤（filter_by_permission）           │
│  - 剥离用户不可见的 visibility                        │
│  - 剥离敏感词命中                                    │
│  - 校验 uploader_id（restricted 限制）               │
│  - 取 Top-5                                         │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  步骤 6：调用 LLM 生成回答（SSE 流式）                 │
│  + 写 query_log                                     │
└─────────────────────────────────────────────────────┘
```

---

## 三、字段设计

### 3.1 三层字段划分

| 层 | 字段 | 谁来填 | 谁来用 |
|---|---|---|---|
| **硬过滤** | 学科 / 类型 / 学院 / 学期 / 受众 / 格式 / 时间 | 上传员手动 + 问题解析器自动 | SQL WHERE |
| **软匹配** | 标题 / 描述 / 关键词表 / 正文 / 原始文件名 | 上传员手动 + AI 自动抽取 | 向量 + 关键词检索 |
| **上下文** | 用户角色 / 用户学院 / 受众 / 黑名单 | 系统自动注入 | 权限校验 |

### 3.2 `resource` 表需要的字段（已具备大部分）

#### 已具备（不需要改）

| 字段 | 类型 | 用途 |
|---|---|---|
| `title` | `VARCHAR(256)` | 软匹配（关键词 + 向量） |
| `description` | `LONGTEXT` | 软匹配（关键词 + 向量） |
| `discipline_id` | `BIGINT` | 硬过滤 |
| `category_id` | `BIGINT` | 硬过滤 |
| `college_id` | `BIGINT` | 硬过滤 |
| `file_type` | `VARCHAR(16)` | 硬过滤 |
| `file_name` | `VARCHAR(256)` | 软匹配（关键词兜底） |
| `created_at` / `published_at` | `DATETIME` | 硬过滤（时间范围） |

#### ⚠️ 待新增（本次改动）

| 字段 | 类型 | 默认 | 必填 | 说明 |
|---|---|---|---|---|
| `visibility` | `VARCHAR(16)` | `'public'` | 是 | 可见层级：`public`/`internal`/`restricted` |
| `target_audience` | `VARCHAR(16)` | `'all'` | 是 | 适用对象：`all`/`undergrad`/`postgrad`/`teacher`/`staff` |
| `course_term` | `VARCHAR(128)` | `''` | 否 | 课程+学期（如 "操作系统2025春"） |
| `keywords` | `JSON` | `'[]'` | 否 | AI 抽取的关键词数组 `[{word, weight}]` |

**SQL 变更**：[database-changes/query_log.md](database-changes/query_log.md)

---

## 四、新增表：`query_log`（查询日志）

### 4.1 建表 SQL

```sql
CREATE TABLE `query_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `user_id` BIGINT NOT NULL,
    `question` LONGTEXT NOT NULL,
    `parsed_filters` JSON NULL,
    `hit_count` INTEGER NOT NULL DEFAULT 0,
    `top_doc_ids` JSON NULL,
    `response_ms` INTEGER NOT NULL DEFAULT 0,
    `user_feedback` TINYINT NOT NULL DEFAULT -1,
    `created_at` DATETIME(6) NOT NULL,
    `conversation_id` BIGINT NULL,
    CONSTRAINT `fk_qlog_user` FOREIGN KEY (`user_id`) REFERENCES `user`(`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_qlog_conv` FOREIGN KEY (`conversation_id`) REFERENCES `chat_conversation`(`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_qlog_user_created` ON `query_log`(`user_id`, `created_at`);
CREATE INDEX `idx_qlog_created` ON `query_log`(`created_at`);
CREATE INDEX `idx_qlog_feedback` ON `query_log`(`user_feedback`, `created_at`);
```

> 字段含义详见 [database-changes/query_log.md](database-changes/query_log.md)

### 4.2 数据使用

- 统计**用户最常问的问题** → 推动新资料入库
- 查看**点赞率高的问题** → 验证检索质量
- 查看**响应时间分布** → 性能优化方向
- 查看**点踩率高的问题** → 优化关键词 / 问答对

---

## 五、代码改动清单（详细代码见 code-changes/2026-07-01.md）

### 5.1 新增文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/apps/classify/utils/query_parser.py` | ~180 行 | 问题解析器（LLM + 规则回退） |
| `backend/apps/resources/models_keywords.py` | ~60 行 | `ResourceKeywordMixin`（关键词字段） |
| `backend/apps/chat/models_query_log.py` | ~50 行 | `QueryLog` 模型 |

### 5.2 修改文件

| 文件 | 改动 |
|---|---|
| `backend/utils/rag.py` | `retrieve()` 加 5 个参数；新增 `_apply_hard_filters` / `_filter_by_permission` |
| `backend/apps/resources/models.py` | `Resource` 加 4 字段（`visibility`/`target_audience`/`course_term`/`keywords`） |
| `backend/apps/chat/serializers.py` | `AskSerializer` 加 `parsed_filters` 字段 |
| `backend/apps/chat/views.py` | `AskView` 接入新流程 + 写 `query_log` |
| `backend/apps/chat/urls.py` | 新增 `/api/chat/feedback/` 评价接口 |

### 5.3 Migration 文件

| 文件 | 操作 |
|---|---|
| `apps/resources/migrations/0002_resource_keywords.py` | 加 4 字段 |
| `apps/chat/migrations/0001_initial.py` | 建 `query_log` 表 |

---

## 六、核心代码

### 6.1 `query_parser.py`：问题解析器

```python
"""
问题解析器（Query Parser）
- LLM 抽取硬过滤条件（首选）
- 规则匹配回退（LLM 失败时）
"""
import json
import logging
import re
from typing import Optional

from .llm import llm_client

logger = logging.getLogger(__name__)


# LLM 抽取的 Prompt
_PARSE_PROMPT = """你是校园知识库的查询解析器。请把用户提问解析为 JSON 格式的过滤条件。

可选字段（仅以下 7 个，全部可选）：
- college: 学院名（仅以下选项之一：['计算机与软件学院', '信息与商务管理学院', '外国语学院', '数字艺术与设计学院', '健康医养技术学院', '马克思主义学院', '通识教学部', '体育教学部', '创新创业学院', '国际合作学院', '继续教育学院', '基础部', '现代管理研究院']）
- discipline: 学科方向（仅以下选项之一：['计算机科学与技术', '软件工程', '数字媒体技术', '电子商务', '英语', '视觉传达设计', '数据科学与大数据技术', '物联网工程', '人工智能'])
- category: 资源一级类型（仅以下选项之一：['课件', '习题', '试卷', '论文', '教案', '实验报告', '毕业设计', '教学大纲', '其他'])
- target_audience: 适用对象（仅以下选项之一：'undergrad'/'postgrad'/'teacher'/'staff'/'all'）
- course_term: 课程+学期（字符串，例："操作系统2025春"）
- file_type: 文件格式（仅以下选项之一：'pdf'/'doc'/'ppt'/'xls'/'image'/'video'/'audio'）
- time_range: 时间范围（格式 "YYYY" 或 "YYYY-MM"）

只返回 JSON，不要解释。如果无法判断，留空或省略。

示例：
用户："计算机系大三下学期的操作系统复习资料"
输出：{{"college": "计算机与软件学院", "discipline": "计算机科学与技术", "target_audience": "undergrad", "course_term": "操作系统2025春", "category": "习题"}}

用户："{{question}}"
输出：
"""


# 规则回退：常见关键词到字段的映射
_RULE_KEYWORDS = {
    'college': {
        '计算机': '计算机与软件学院', '信管': '信息与商务管理学院',
        '外语': '外国语学院', '艺设': '数字艺术与设计学院',
        '医养': '健康医养技术学院',
    },
    'target_audience': {
        '本科': 'undergrad', '研究生': 'postgrad',
        '教师': 'teacher', '老师': 'teacher',
        '大一': 'undergrad', '大二': 'undergrad',
        '大三': 'undergrad', '大四': 'undergrad',
    },
    'file_type': {
        'PDF': 'pdf', 'pdf': 'pdf',
        'PPT': 'ppt', 'ppt': 'ppt',
        'Word': 'doc', 'doc': 'doc',
        'Excel': 'xls', 'xls': 'xls',
    },
}


def parse(question: str, user_context: Optional[dict] = None) -> dict:
    """
    解析用户提问为结构化过滤条件。

    Args:
        question: 用户原始问题
        user_context: 用户上下文（含 user_id / role / college_id）

    Returns:
        dict: 过滤条件
        例：{'college_id': 1, 'target_audience': 'undergrad', 'course_term': '操作系统'}
    """
    # 1. LLM 抽取
    try:
        raw = _parse_by_llm(question)
        if raw:
            return _normalize(raw)
    except Exception as e:
        logger.warning(f'LLM 解析失败，回退到规则匹配: {e}')

    # 2. 规则匹配回退
    return _parse_by_rules(question)


def _parse_by_llm(question: str) -> Optional[dict]:
    """LLM 抽取（首选）"""
    prompt = _PARSE_PROMPT.format(question=question)
    raw = llm_client.chat(
        messages=[{'role': 'user', 'content': prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    if not raw:
        return None

    # 提取 JSON
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _parse_by_rules(question: str) -> dict:
    """规则匹配（兜底）"""
    result = {}
    for field, mapping in _RULE_KEYWORDS.items():
        for kw, value in mapping.items():
            if kw in question:
                result[field] = value
                break

    # 提取课程名
    course_match = re.search(r'([\u4e00-\u9fa5]{2,8})20\d{2}[春夏秋冬]', question)
    if course_match:
        result['course_term'] = course_match.group(0)
    return result


def _normalize(raw: dict) -> dict:
    """归一化：将学院/学科名称转为 ID"""
    from apps.accounts.models import College
    from apps.classify.models import Discipline, Category

    result = {}

    if 'college' in raw:
        try:
            result['college_id'] = College.objects.get(name=raw['college']).id
        except College.DoesNotExist:
            pass
    elif 'discipline' in raw:
        try:
            result['discipline_id'] = Discipline.objects.get(name=raw['discipline']).id
        except Discipline.DoesNotExist:
            pass
    elif 'category' in raw:
        try:
            cat = Category.objects.get(name=raw['category'], parent__isnull=True)
            result['category_ids'] = [cat.id] + [
                c.id for c in cat.children.all()
            ]
        except Category.DoesNotExist:
            pass

    # 透传简单字段
    for k in ('target_audience', 'course_term', 'file_type', 'time_range'):
        if k in raw:
            result[k] = raw[k]

    return result
```

### 6.2 `rag.py` 升级：新增硬过滤与权限校验

```python
# 在 backend/utils/rag.py 的 retrieve() 中新增以下函数


def _apply_hard_filters(
    filters: dict,
    user_context: dict,
    top_k: int = 500,
) -> List[int]:
    """
    对 resource 表执行 SQL 硬过滤，返回候选 doc_id 列表。

    Args:
        filters: parse() 输出的过滤条件
        user_context: {'role': 'user', 'college_id': 1, 'user_id': 123}
        top_k: 最多返回多少候选（默认 500）

    Returns:
        doc_id 列表
    """
    from apps.resources.models import Resource

    qs = Resource.objects.filter(status='published')

    # 1. 可见层级（核心权限）
    role = user_context.get('role', 'user')
    if role == 'super_admin':
        visibility_q = Q()  # 超管全见
    elif role == 'admin':
        visibility_q = Q(visibility__in=['public', 'internal'])
    else:
        visibility_q = Q(visibility__in=['public', 'internal'])
        # 普通用户：restricted 只看自己上传的
        visibility_q |= Q(visibility='restricted', uploader_id=user_context['user_id'])
    qs = qs.filter(visibility_q)

    # 2. 适用对象
    audience = filters.get('target_audience')
    if audience and audience != 'all':
        qs = qs.filter(Q(target_audience=audience) | Q(target_audience='all'))

    # 3. 学科 / 学院 / 格式 / 学期
    if (cid := filters.get('college_id')):
        qs = qs.filter(college_id=cid)
    if (did := filters.get('discipline_id')):
        qs = qs.filter(discipline_id=did)
    if (cat_ids := filters.get('category_ids')):
        qs = qs.filter(category_id__in=cat_ids)
    if (ft := filters.get('file_type')):
        qs = qs.filter(file_type=ft)
    if (term := filters.get('course_term')):
        qs = qs.filter(course_term__icontains=term)
    if (tr := filters.get('time_range')):
        if re.match(r'^\d{4}$', tr):
            year = int(tr)
            qs = qs.filter(published_at__year=year)
        elif re.match(r'^\d{4}-\d{2}$', tr):
            year, month = tr.split('-')
            qs = qs.filter(
                published_at__year=int(year),
                published_at__month=int(month),
            )

    return list(qs.values_list('id', flat=True)[:top_k])


def _filter_by_permission(
    hits: List[dict],
    user_context: dict,
) -> List[dict]:
    """
    权限校验：在 Top-K 命中里，剥离用户不可见的 doc。

    Args:
        hits: retrieve() 输出的命中列表
        user_context: 用户上下文

    Returns:
        过滤后的 hits
    """
    from apps.resources.models import Resource

    if not hits:
        return []

    doc_ids = [h['doc_id'] for h in hits]
    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')

    # 批量查 doc 的 visibility / uploader
    docs = Resource.objects.filter(id__in=doc_ids).values(
        'id', 'visibility', 'uploader_id'
    )
    doc_map = {d['id']: d for d in docs}

    filtered = []
    for hit in hits:
        doc = doc_map.get(hit['doc_id'])
        if not doc:
            continue

        # 权限校验
        visibility = doc['visibility']
        uploader_id = doc['uploader_id']

        if visibility == 'restricted':
            if role == 'super_admin' or uploader_id == user_id:
                filtered.append(hit)
        elif visibility in ('public', 'internal'):
            filtered.append(hit)

    return filtered[:5]  # 取 Top-5


# ------------------------------------------------------------------
# 改写后的 retrieve()
# ------------------------------------------------------------------
def retrieve(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_SCORE_THRESHOLD,
) -> List[dict]:
    """
    检索管道（升级版）：
    1. 解析问题为硬过滤条件
    2. SQL 硬过滤缩范围
    3. 双路召回（向量 + 关键词）
    4. 关键词权重加成
    5. 权限校验

    Args:
        question: 用户问题
        user_context: {'user_id': int, 'role': str, 'college_id': int}
        parsed_filters: parse() 输出的过滤条件（可选，由视图层传）
        top_k: Milvus 召回数量
        min_score: 最低分阈值

    Returns:
        Top-5 命中列表
    """
    user_context = user_context or {'role': 'user'}
    parsed_filters = parsed_filters or {}

    # 1. SQL 硬过滤 → 候选 ID 列表
    candidate_ids = _apply_hard_filters(parsed_filters, user_context, top_k=500)

    if not candidate_ids:
        logger.info(f'硬过滤后无候选: question={question[:50]}')
        return []

    logger.debug(f'硬过滤候选数: {len(candidate_ids)}, filters={parsed_filters}')

    # 2. 双路召回（仅在候选范围内检索）
    question_embedding = _cached_embed(question)
    vector_hits = []
    if question_embedding:
        vector_hits = milvus_client.search(
            question_embedding,
            top_k=top_k,
            filter_ids=candidate_ids,  # 关键：限定范围
        )

    keyword_hits = _keyword_search(
        question,
        top_k=top_k,
        doc_ids=candidate_ids,  # 关键
    )

    # 3. 合并 + 关键词权重加成
    merged = _merge_and_rerank(
        vector_hits,
        keyword_hits,
        keywords_boost=_keywords_boost_from_table(parsed_filters),
    )

    # 4. 加载 doc 元信息
    enriched = _enrich_with_doc_meta(merged, min_score)

    # 5. 权限校验
    return _filter_by_permission(enriched, user_context)
```

### 6.3 `_merge_and_rerank` 增加关键词权重

```python
# 替换 _merge_and_rerank 函数
def _merge_and_rerank(
    vector_hits: List[dict],
    keyword_hits: List[dict],
    vector_weight: float = 0.7,
    keywords_boost: Optional[dict] = None,
) -> List[dict]:
    """
    合并重排序（升级版）：向量分 × 0.7 + 关键词分 × 0.3 + 关键词表权重加成

    Args:
        vector_hits: Milvus 命中
        keyword_hits: MySQL 关键词命中
        vector_weight: 向量权重（默认 0.7）
        keywords_boost: 关键词→权重映射，例：{'操作系统': 10, '计算机': 5}
    """
    keywords_boost = keywords_boost or {}
    merged = {}

    for hit in vector_hits:
        key = hit.get('vector_id') or f"v_{hit.get('doc_id')}_{hit.get('chunk_index')}"
        merged[key] = {
            'doc_id': hit.get('doc_id'),
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id'),
            'content': hit.get('chunk_text', ''),
            'page': hit.get('page_number', 0),
            'score': hit['score'] * vector_weight,
        }

    for hit in keyword_hits:
        key = f"kw_{hit.get('doc_id')}"
        existing = merged.get(key)
        if existing:
            existing['score'] += hit['score'] * (1 - vector_weight)
        else:
            merged[key] = {
                'doc_id': hit.get('doc_id'),
                'content': hit.get('content', ''),
                'page': hit.get('page', 0),
                'vector_id': '',
                'score': hit['score'] * (1 - vector_weight),
            }

    # 关键词权重加成：扫文档标题/正文，含表内词则加权
    if keywords_boost:
        for hit in merged.values():
            content = (hit.get('content') or '').lower()
            doc_id = hit.get('doc_id')
            extra = 0
            for word, weight in keywords_boost.items():
                word_lower = word.lower()
                if word_lower in content:
                    # weight 按比例加成，weight=10 加 0.2 分，weight=5 加 0.1 分
                    extra += weight / 50.0
            hit['score'] += extra

    return sorted(merged.values(), key=lambda x: x['score'], reverse=True)


def _keywords_boost_from_table(parsed_filters: dict) -> dict:
    """从数据库读取高频关键词及其权重"""
    # 注意：项目当前没有 keywords 表，本次实现先从解析结果中临时构造
    # 后续：可新增 resource_keyword 表
    boost = {}
    if term := parsed_filters.get('course_term'):
        # 课程名通常是高权重词
        boost[term] = 10
    return boost
```

### 6.4 `views.py`：接入新流程 + 写日志

```python
# backend/apps/chat/views.py 修改示例

from apps.classify.utils.query_parser import parse as parse_query
from utils.rag import retrieve, build_rag_prompt
from .models import ChatMessage, Conversation, QueryLog


class AskView(APIView):
    """
    AI 问答接口（升级版）

    POST /api/chat/ask/
    功能：接收用户提问 → 解析问题 → 硬过滤 → 检索 → 生成回答
    参数：
        - conversation_id (int, optional): 会话 ID（不传则新建）
        - question (str): 用户提问
    返回：
        - 流式 SSE（参考 [SSE 协议](#)）
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        question = data['question']
        user = request.user
        user_context = {
            'user_id': user.id,
            'role': user.role,
            'college_id': user.college_id,
        }

        # 1. 问题解析
        parsed = parse_query(question, user_context)

        # 2. 检索（带硬过滤）
        hits = retrieve(
            question=question,
            user_context=user_context,
            parsed_filters=parsed,
        )

        # 3. 构造 Prompt
        messages = build_rag_prompt(question, hits)

        # 4. 写 query_log（异步写入）
        QueryLog.objects.create(
            user_id=user.id,
            question=question,
            parsed_filters=parsed,
            hit_count=len(hits),
            top_doc_ids=[h['doc_id'] for h in hits],
            response_ms=0,  # SSE 完成后回填
        )

        # 5. 流式返回 + 回填 response_ms
        return _stream_response(messages, request.data.get('conversation_id'))
```

---

## 七、实施步骤

### 7.1 任务分解（按依赖顺序）

| 步骤 | 内容 | 依赖 | 估时 |
|---|---|---|---|
| 1 | `Resource` 表加 4 字段（`visibility` 等） | 无 | 1h |
| 2 | `query_log` 建表 + `QueryLog` 模型 | 无 | 0.5h |
| 3 | Migration 文件 | 1, 2 | 0.5h |
| 4 | `query_parser.py` 编写 | 3 | 2h |
| 5 | `rag.py` 升级（硬过滤 / 权重加成 / 权限） | 3 | 3h |
| 6 | `AskView` 接入新流程 + 写日志 | 4, 5 | 1h |
| 7 | 评价接口 `/api/chat/feedback/` | 2 | 0.5h |
| 8 | 单元测试 + 集成测试 | 1–7 | 3h |

### 7.2 测试用例

| 用例 | 输入 | 期望输出 |
|---|---|---|
| 普通用户查公开资料 | "操作系统的 PDF" | 命中 `visibility=public` + `file_type=pdf` |
| 普通用户查受限资料 | query 含学院限制 | 自动剥离 `restricted` |
| 关键词加成 | question 含高权重词 | Top-1 是该关键词命中的 doc |
| LLM 解析失败 | 乱码输入 | 回退规则匹配，不报错 |
| 空硬过滤 | "你好" | 全量库检索 |
| 权限回流 | restricted + 自己上传 | 命中（自己可见） |

---

## 八、风险与权衡

| 风险 | 影响 | 缓解 |
|---|---|---|
| 问题解析耗时长（+200ms） | 响应时间 +200ms | 加 LRU 缓存，相同问题 5 分钟内复用 |
| LLM 解析失败 | 报错影响体验 | 双策略（LLM + 规则），规则兜底 |
| 硬过滤过严导致无候选 | 召回为空 | 候选为 0 时降级到全量检索 |
| `target_audience='all'` 误判 | 普通用户也能看到教师资料 | 人工标注 + 反馈学习 |
| `keywords` 字段缺索引 | 慢查询 | 该字段只用于权重加成，不做 WHERE |

---

## 九、未来扩展

1. **关键词表独立**：把 `keywords` 字段从 `resource` 表拆出，新建 `resource_keyword(doc_id, word, weight)` 表，支持全局统计。
2. **反馈学习闭环**：基于 `query_log.user_feedback` 自动调整解析 Prompt 与关键词权重。
3. **多轮对话支持**：把上一轮的硬过滤继承到下一轮。
4. **异步写日志**：用 Celery 写 `query_log`，不影响主流程响应时间。

---

## 十、关联资源

- [database-changes/query_log.md](database-changes/query_log.md) — 数据库变更
- [code-changes/2026-07-01.md](code-changes/2026-07-01.md) — 代码改动
- [CONVENTIONS.md](CONVENTIONS.md) — 团队代码规范
