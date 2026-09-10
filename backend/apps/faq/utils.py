"""
FAQ 提取管道辅助函数

依赖:
- utils.llm.llm_client (DeepSeek API 封装)
- numpy (向量运算)
"""
import logging
from typing import List, Tuple, Optional

import numpy as np
from django.conf import settings
from utils.llm import llm_client

logger = logging.getLogger(__name__)


class QuestionCluster:
    """问题簇 — 由相似问题聚合而成"""
    def __init__(self, questions: List[str], answers: List[str], vector: List[float]):
        self.questions = questions      # 所有原始问题变体
        self.answers = answers          # 对应的 AI 回答
        self.vector = vector            # 簇心向量（第一个问题的向量）
        self.size = len(questions)      # 簇大小

    def to_dict(self) -> dict:
        return {
            'questions': self.questions,
            'answers': self.answers,
            'size': self.size,
        }


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def embed_questions(questions: List[str]) -> List[List[float]]:
    """
    批量获取问题向量。

    Args:
        questions: 问题文本列表

    Returns:
        对应的向量列表
    """
    if not questions:
        return []

    vectors = llm_client.embed(questions)
    logger.debug(f'Embedding {len(questions)} 个问题完成')
    return vectors


def cluster_similar_questions(
    question_answer_pairs: List[Tuple[str, str]],
    similarity_threshold: float = None,
) -> List[QuestionCluster]:
    """
    将相似问题聚类（阶段2：向量聚类）。

    Args:
        question_answer_pairs: [(问题, 对应AI回答), ...]
        similarity_threshold: 相似度阈值，默认 settings.FAQ_CLUSTER_THRESHOLD (0.85)

    Returns:
        问题簇列表
    """
    if similarity_threshold is None:
        similarity_threshold = getattr(settings, 'FAQ_CLUSTER_THRESHOLD', 0.85)

    if not question_answer_pairs:
        return []

    questions = [pair[0] for pair in question_answer_pairs]
    vectors = embed_questions(questions)

    if not vectors or len(vectors) != len(questions):
        logger.error('Embedding 失败，返回空簇')
        return []

    # 贪心聚类：依次处理每个问题
    clusters: List[QuestionCluster] = []
    assigned = [False] * len(questions)

    for i in range(len(questions)):
        if assigned[i]:
            continue

        cluster_questions = [questions[i]]
        cluster_answers = [question_answer_pairs[i][1]]
        assigned[i] = True

        for j in range(i + 1, len(questions)):
            if assigned[j]:
                continue

            sim = cosine_similarity(vectors[i], vectors[j])
            if sim >= similarity_threshold:
                cluster_questions.append(questions[j])
                cluster_answers.append(question_answer_pairs[j][1])
                assigned[j] = True

        clusters.append(QuestionCluster(
            questions=cluster_questions,
            answers=cluster_answers,
            vector=vectors[i],
        ))

    logger.info(f'聚类完成: {len(question_answer_pairs)} 个问题 → {len(clusters)} 个簇')
    for idx, c in enumerate(clusters):
        logger.debug(f'  簇 {idx+1}: size={c.size}, 代表="{c.questions[0][:40]}..."')

    return clusters


def filter_by_ratio(
    clusters: List[QuestionCluster],
    total_count: int,
    min_ratio: float = None,
    max_count: int = None,
) -> List[QuestionCluster]:
    """
    按比例筛选高频簇（阶段3）。

    Args:
        clusters: 问题簇列表
        total_count: 总提问数
        min_ratio: 最小占比阈值（百分比），默认 settings.FAQ_MIN_RATIO
        max_count: 最大保留数，默认 settings.FAQ_MAX_CLUSTERS

    Returns:
        筛选后的高频问题簇（按占比降序）
    """
    min_ratio = min_ratio or getattr(settings, 'FAQ_MIN_RATIO', 0.5)
    max_count = max_count or getattr(settings, 'FAQ_MAX_CLUSTERS', 20)

    if total_count <= 0:
        return []

    # 计算占比
    scored = []
    for cluster in clusters:
        ratio = (cluster.size / total_count) * 100.0
        scored.append((cluster, ratio))

    # 过滤 + 排序
    filtered = [(c, r) for c, r in scored if r >= min_ratio]
    filtered.sort(key=lambda x: x[1], reverse=True)

    # 取 Top-K
    top = filtered[:max_count]

    logger.info(
        f'比例筛选: {len(clusters)} 簇 → {len(top)} 簇通过 '
        f'(threshold={min_ratio}%, top={max_count})'
    )

    return [c for c, _ in top]


def get_faq_category_names() -> List[str]:
    """获取全部 FAQ 分类名（用于约束 LLM 输出），按 sort_order 排序。"""
    from .models import FAQCategory
    return list(FAQCategory.objects.order_by('sort_order', 'id').values_list('name', flat=True))


def resolve_category_id(category_name) -> Optional[int]:
    """将 LLM 返回的分类名解析为 FAQCategory.id，匹配不上返回 None。"""
    if not category_name:
        return None
    try:
        from .models import FAQCategory
        name = str(category_name).strip()
        category = FAQCategory.objects.filter(name=name).first()
        if not category:
            category = FAQCategory.objects.filter(name__icontains=name).first()
        return category.id if category else None
    except Exception as e:
        logger.warning(f'解析 FAQ 分类名失败: {e}')
        return None


def generate_faq_from_source(title: str, content: str, source_url: str = '') -> Optional[dict]:
    """根据官网文档生成单条 FAQ 草稿，答案必须有来源依据。"""
    if not content or not content.strip():
        return None

    category_names = get_faq_category_names()
    category_rule = ''
    category_field = ''
    if category_names:
        category_rule = f"分类必须从以下名称中选一个并原样输出：{'、'.join(category_names)}。\n"
        category_field = '"category_name":"分类名",'

    system_prompt = f"""你是校园官网 FAQ 编辑。只能依据用户提供的文档内容生成一条 FAQ。
不得补充文档中没有的事实、电话、网址、时间或政策；信息不足时返回 null。
{category_rule}输出严格 JSON，不要 Markdown 或其他文字：
{{"question":"标准化问题","answer":"依据原文整理的答案",{category_field}"tags":["标签"],"college_name":"学院名称或全局"}}
问题应是学生或教职工会实际提出的办事问题，答案简洁、可执行；答案中不得声称文档未提及的内容。"""
    user_prompt = (
        f"文档标题：{title[:300]}\\n来源：{source_url[:500]}\\n"
        f"文档内容：\\n{content[:12000]}\\n\\n"
        "若文档不包含明确的办事/服务信息，返回 null。"
    )
    try:
        import json as _json
        response = llm_client.chat(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        if not response:
            return None
        text = response.strip()
        if text.lower() == 'null':
            return None
        if '```' in text:
            text = text.split('```json')[-1].split('```')[0].strip()
        result = _json.loads(text)
        question = str(result.get('question') or '').strip()
        answer = str(result.get('answer') or '').strip()
        if not question or not answer:
            return None
        return {
            'question': question[:500],
            'answer': answer,
            'tags': result.get('tags', []) if isinstance(result.get('tags', []), list) else [],
            'category_name': str(result.get('category_name') or '').strip(),
            'college_name': str(result.get('college_name') or '全局'),
        }
    except Exception as exc:
        logger.warning('官网文档 FAQ 生成失败 title=%s: %s', title[:50], exc)
        return None


def generate_faq_draft(cluster: QuestionCluster) -> Optional[dict]:
    """
    对单个高频问题簇调用 LLM 生成 FAQ 草稿（阶段4）。

    Args:
        cluster: 问题簇

    Returns:
        {
            'question': str,           # 标准化问题
            'answer': str,             # 标准答案
            'tags': List[str],         # 关键词标签
            'category_id': int | None, # 所属分类ID，None=未分类
            'college_id': int | None,  # 推断的学院ID，None=全局
        }
        失败返回 None
    """
    # 构建 LLM prompt
    questions_text = '\n'.join(f'- {q}' for q in cluster.questions[:10])
    answers_text = '\n'.join(f'- {a[:200]}' for a in cluster.answers[:5] if a)

    category_names = get_faq_category_names()
    category_rule = ''
    category_field = ''
    if category_names:
        category_rule = (
            f"5. 从以下 FAQ 分类中选择最匹配的一个，必须原样输出分类名称：{'、'.join(category_names)}\n"
        )
        category_field = '    "category_name": "分类名称",\n'

    system_prompt = f"""你是一个 FAQ 生成助手。请根据用户反复询问的同类问题变体和已有回答，
生成一份标准化的 FAQ 条目。

要求：
1. 将多个问题变体归纳为一个清晰、简洁的标准化问题
2. 综合已有回答，生成完整的标准答案（200-500字，条理清晰）
3. 提取 3-5 个关键词标签
4. 判断是否与特定学院相关：
   - 如果问题明确提到某个学院名称，输出该学院名称
   - 如果是通用问题（如"如何重置密码"），输出"全局"
{category_rule}{'6' if category_names else '5'}. 以 JSON 格式输出，不要包含其他内容"""

    user_prompt = f"""问题变体（共 {cluster.size} 条，以下展示前 10 条）：
{questions_text}

已有回答（前 5 条摘要）：
{answers_text}

请输出如下 JSON（不要有其他内容）：
{{
    "question": "标准化后的问题",
    "answer": "完整的标准答案",
{category_field}    "tags": ["标签1", "标签2", "标签3"],
    "college_name": "学院名称 或 '全局'"
}}"""

    try:
        import json as _json

        response = llm_client.chat(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        if not response:
            logger.error(f'LLM 生成 FAQ 草稿失败：返回空')
            return None

        # 提取 JSON（LLM 可能用 ```json 包裹）
        text = response.strip()
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        result = _json.loads(text)

        # 解析 college_id
        college_id = None
        college_name = result.get('college_name', '全局')
        if college_name and college_name != '全局':
            try:
                from apps.accounts.models import College
                college = College.objects.filter(name__icontains=college_name).first()
                if college:
                    college_id = college.id
            except Exception as e:
                logger.warning(f'解析学院名称失败: {e}')

        # 解析 category_id（分类表为空时跳过）
        category_id = resolve_category_id(result.get('category_name')) if category_names else None

        return {
            'question': result.get('question', cluster.questions[0][:500]),
            'answer': result.get('answer', ''),
            'tags': result.get('tags', []),
            'category_id': category_id,
            'college_id': college_id,
        }

    except Exception as e:
        logger.error(f'LLM 生成 FAQ 草稿失败: {e}')
        return None


def classify_faq_category(question: str, answer: str = '') -> Optional[str]:
    """
    调用 LLM 为单条 FAQ 判定分类（存量数据回填用）。

    Args:
        question: FAQ 问题
        answer: FAQ 答案（可空）

    Returns:
        分类名（限定 faq_category 表内的名称），不匹配返回 None
    """
    category_names = get_faq_category_names()
    if not category_names or not question or not question.strip():
        return None

    system_prompt = """你是校园 FAQ 分类助手。根据给定的问题和答案，从候选分类中选出最匹配的一个。
只能原样输出候选分类中的名称，若都不匹配则输出 null。
输出严格 JSON，不要 Markdown 或其他文字：
{"category_name":"分类名"}"""
    user_prompt = (
        f"候选分类：{'、'.join(category_names)}\n\n"
        f"问题：{question[:500]}\n答案：{(answer or '')[:1000]}"
    )

    try:
        import json as _json
        response = llm_client.chat(
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.1,
            max_tokens=100,
        )
        if not response:
            return None
        text = response.strip()
        if text.lower() == 'null':
            return None
        if '```' in text:
            text = text.split('```json')[-1].split('```')[0].strip()
        result = _json.loads(text)
        name = str(result.get('category_name') or '').strip()
        return name if name in category_names else None
    except Exception as exc:
        logger.warning('FAQ 分类判定失败 question=%s: %s', question[:50], exc)
        return None
