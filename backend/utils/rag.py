"""
RAG 检索管道
- 问题向量化（含 LRU 缓存）→ Milvus 搜索 → 关键词匹配 → 重排序 → Top-K 结果
- 上下文组装：将检索结果拼接为 LLM Prompt
"""
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from django.conf import settings

from .llm import llm_client
from .milvus import milvus_client
from .timefmt import format_business_time, month_bounds, to_iso, year_bounds

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 历史意图检测
# ------------------------------------------------------------------
def _detect_history_intent(question: str) -> bool:
    """检测用户是否有查看历史数据的意图（关键词匹配）"""
    history_keywords = ['历年', '所有', '历史', '全部', '以往', '过去', '各年']
    return any(keyword in question for keyword in history_keywords)


# ------------------------------------------------------------------
# 纯时间/日期问题检测
# ------------------------------------------------------------------
# 这类问题应由 build_system_context() 注入的「当前时间」直接回答，无需 RAG 检索；
# 否则「现在/时间」等词会在文档标题/描述里乱匹配，命中无关文档、污染参考答案。
# 用「整句精确匹配」而非前缀匹配，避免误伤「现在几点钟有课」「图书馆现在几点关门」等复合问题。
_TIME_CORE_PATTERNS = {
    '现在几点', '几点', '现在几点钟', '几点钟',
    '现在时间', '现在时间是多少', '现在是多少', '现在是多少点',
    '现在什么时间', '现在什么时候', '现在是什么时候', '现在是什么时间',
    '当前时间', '当前时间是多少',
    '现在几号', '现在日期', '今天几号', '今天日期', '今天星期几', '今天周几',
    '今天是什么日子', '今天是什么日期', '今天是几号', '今天是星期几', '今天是周几',
    '今天多少号', '今天几月几号', '今天是什么时间', '今天什么时候',
}


def _is_time_question(question: str) -> bool:
    """检测是否为纯时间/日期类问题。

    规则：整句（去标点、去礼貌前缀「请问」、去结尾语气词「了/啊/呢」）精确落在
    时间问句白名单里才拦截，避免误伤「图书馆现在几点关门」「现在几点钟有课」等
    需要检索的复合问题。
    """
    import re as _re
    q = _re.sub(r'[？?！!。，,；;、\s]', '', (question or '')).strip()
    if not q or len(q) > 12:
        return False
    if q.startswith('请问'):
        q = q[2:]
    core = q.rstrip('了啊呢呀吧')
    return core in _TIME_CORE_PATTERNS


# ------------------------------------------------------------------
# Embedding 缓存（LRU + TTL，避免相同问题重复调 Embedding API）
# ------------------------------------------------------------------
_EMBEDDING_CACHE_TTL = 300  # 5 分钟
_EMBEDDING_CACHE_MAX_SIZE = 500

_embedding_cache: Dict[str, tuple] = {}
_embedding_cache_lock = threading.Lock()


def _cached_embed(question: str) -> Optional[List[float]]:
    """带 LRU + TTL 缓存的问题向量化"""
    now = time.time()
    with _embedding_cache_lock:
        # 命中且未过期
        if question in _embedding_cache:
            vec, ts = _embedding_cache[question]
            if now - ts < _EMBEDDING_CACHE_TTL:
                return vec
            del _embedding_cache[question]

    # 未命中，调用真实 API（query 侧加 bge 检索前缀）
    vec = llm_client.embed_one(question, is_query=True)
    if vec:
        with _embedding_cache_lock:
            # 超过容量限制时淘汰最旧的条目
            if len(_embedding_cache) >= _EMBEDDING_CACHE_MAX_SIZE:
                oldest = min(_embedding_cache.keys(), key=lambda k: _embedding_cache[k][1])
                del _embedding_cache[oldest]
            _embedding_cache[question] = (vec, now)
    return vec


# ------------------------------------------------------------------
# RAG System Prompt（与设计文档第 14.2 节⑦对齐）
# ------------------------------------------------------------------
# 内建知识库：东软官网核心信息（手工维护，代替爬虫）
# 增删改后重启即生效，无需爬虫、无需向量库
# ------------------------------------------------------------------
NEUSOFT_KNOWLEDGE = """## 学校概况
- 学校全称：成都东软学院（Chengdu Neusoft University）
- 官网：https://www.nsu.edu.cn
- 成立时间：2003年（前身为成都东软信息技术职业学院），2011年升格为本科院校
- 办学性质：全日制民办普通本科高等院校，由东软集团出资举办
- 办学层次：本科为主，兼有专科，正在筹备硕士研究生教育
- 所在地：四川省成都市都江堰市青城山镇东软大道1号（国家5A级景区内）
- 占地面积：约52.85万平方米，建筑面积约36.33万平方米
- 在校生：约21000余人（2025年数据）
- 教职工：1076人，其中专任教师815人，外聘教师398人；专任教师中"双师型"教师137人，高级职称245人，硕士/博士754人（占比92.52%）
- 校训：精勤博学、学以致用
- 校长：张应辉
- 学校标识码：4151012636，四川省招生代码：5177
- 历史沿革：2003年获批首批35所国家级示范性软件职业技术学院之一；2008年汶川地震后异地复课；2011年升格本科，成为四川省第一所独立设置的民办本科高校；2012年设立博士后创新实践基地；2021年通过教育部本科教学工作合格评估
- 学科门类：工学、管理学、艺术学、文学、医学5大学科门类
- 本科专业：30个（2025年），新增机器人工程、数据科学与大数据技术（中外合作办学）；另有2个高职专科专业
- 累计培养毕业生：3万余人，本科毕业生平均就业率94.5%
- 合作企业：华为、百度、腾讯、京东、阿里巴巴、微软、英特尔、IBM、SAP等
- TOPCARES一体化人才培养模式：知识、能力、素养八大能力体系
- "1321"特色学期：1学年3学期（2个理论学期+1个实践学期），三校跨校游学（成都、大连、佛山）

## 院系设置（13个二级学院）

### 1. 计算机与软件学院
- 专业：软件工程（国家级一流本科专业建设点、一本招生）、计算机科学与技术（含本硕连读国际班）、网络工程（省一流）、物联网工程（省一流）、网络空间安全
- 特色：四川省首批特色化示范性软件学院（2020年）；网络安全产业学院为四川省首批现代产业学院（2022年）
- 毕业生入职：百度、阿里、字节跳动、美团、网易、奇安信、科大讯飞、SAP

### 2. 智能科学与工程学院
- 专业：信息工程、数据科学与大数据技术、大数据管理与应用、人工智能、智能科学与技术、机器人工程（2025年新增）
- 特色：省级人工智能实验教学示范中心，下设智能应用、人工智能与并行计算、人工智能创新实验室
- 中外合作办学：与美国东康涅狄格州立大学合作"3+1"项目，前三年学费68000元/年

### 3. 信息与商务管理学院
- 专业：信息管理与信息系统、电子商务、财务管理（含本硕连读国际班）、人力资源管理、供应链管理
- 特色："IT+管理"、"IT+商务"新工科/新文科融合模式；合作企业包括马士基、格力、苏宁、用友、金蝶、京东、阿里

### 4. 数字艺术与设计学院
- 专业：数字媒体技术、工业设计、虚拟现实技术、动画、艺术与科技、数字媒体艺术、视觉传达设计
- 特色：国家火炬计划成都数字娱乐产业人才培训基地；拥有虚拟现实、元宇宙中心、动画电影等22个实验实训室和12个产学研用工作室
- 参与制作《流浪地球2》《西游记之再世妖王》等商业电影；历年毕业去向落实率超过94%

### 5. 外国语学院
- 专业：商务英语、英语、日语
- 特色："外语+IT"、"外语+商务"、"外语+艺术"培养模式；90%以上教师有985/211及海外留学背景，硕士博士比例达95%

### 6. 健康医疗科技学院
- 专业：健康服务与管理、医学影像技术、智能医学工程、医学信息工程
- 特色："IT+健康管理"、"IT+医疗科技"融合模式；拥有一所三级附属医院和多所三甲教学实习医院
- 联合电子科技大学大数据研究中心推出"AI+数据科学"跨学科培养计划

### 7. 高等职业技术学院
- 专科专业：软件技术、影视动画

### 8-13. 其他教学单位
- 基础教学学院、马克思主义学院、创新创业学院、国际教育学院、继续教育（培训）学院、退役军人教育学院（2020年成立，四川省首家）

## 校园服务
- 图书馆（华天图书馆）：周一至周日8:00-22:00；馆藏纸质及电子文献资源330万册；拥有超星移动图书馆、万方医学网、中国知网等电子期刊数据库；馆内设有咖啡店、文创空间
- 实验室：82个实体实验室，教学科研仪器设备总值约1.22亿元；含2个省级实验教学示范中心（网络工程、人工智能）、1个省级虚拟仿真实验教学中心、SOVO国家级众创空间
- 校外实习基地：193个，含4个省级大学生校外实践基地
- 校园网：全校Wi-Fi覆盖；需办理校园卡获取优惠流量套餐和宽带
- 教务系统：https://bkshpg.nsu.edu.cn
- 招生信息网：https://zs.nsu.edu.cn

## 宿舍与生活
- 宿舍配置：大部分为四人间，上床下桌，配备空调、独立卫浴（干湿分离）。少数D1-D3宿舍为公共卫浴
- 供电时间：周中+周日6:00-23:00，周五周六延长至23:30（空调不断电）；热水供应：早6:00-8:00、午12:00-14:00、晚18:00-23:00
- 食堂：两个大型食堂（新食堂B1和老食堂A9），各三层，涵盖川菜、面食、小吃、旋转小火锅等
- 校外美食街：老校区旁美食街、新校区门口小吃街、星光夜市
- 快递：校内两个菜鸟驿站、京东和邮政快递点
- 体育设施：篮球场、足球场、排球场、羽毛球场、网球场、乒乓球室

## 学生社团
- 科技类：计算机网络协会、信息安全协会（可参加CTF竞赛）
- 文艺类：米洛音乐社、锦繁动漫社、汉服社、NU-KPOP社、问道茶社
- 运动类：轮滑社、跆拳道社、篮球社、电竞社、滑板社、排球社
- 实践类：营销协会、创新创业社团（对接校企项目）
- 公益类：心理协会、公益服务社

## 奖助学金
- 华天奖学金：10000元/人·年
- 东软睿新奖学金：一等奖5000元 / 二等奖2000元 / 三等奖1000元
- 国家奖学金：10000元/人·年
- 国家励志奖学金：6000元/人·年
- 国家助学金：2500～4900元/人·年
- 生源地信用助学贷款：最高20000元/年（在校期间国家贴息）
- 每年约30%在校生可获得各类奖助学金

## 学费标准
- 普通本科专业：18000～20000元/年
- 中外合作办学（前三年）：68000元/年
- 艺术类专业：20000元/年

## 录取规则（2025年）
- 分数优先，不设专业级差
- 平行志愿批次调档比例100%
- 认可国家和各省加分政策
- 外语语种不限，非外语专业公共必修课为大学英语
- 男女比例不限
- 新生入校后可按规定申请转专业

## 学期与假期安排
- 开学：每年9月初
- 期末：1月（秋季学期）/ 6-7月（春季学期）
- 毕业典礼：每年6月中下旬
- 暑假：7月中旬至8月底
- 寒假：1月中旬至2月底
- 军训：开学即训，时长约2周

## 联系方式
- 地址：四川省成都市都江堰市青城山镇东软大道1号（邮编：611844）
- 招生电话：028-64888001 / 028-64888002 / 028-64888003
- 招生邮箱：edu@nsu.edu.cn
- 招生信息网：https://zs.nsu.edu.cn"""

SYSTEM_PROMPT = """你是成都东软学院的AI智能助手，负责回答学院教学、科研、管理相关问题以及常识性问题。

{context}

## 学校内建知识库（可直接使用）
{knowlege}

## 外部检索资料（补充信息）
---
{references}
---

指令：
1. 学院概况、院系专业、校园服务等问题，优先用内建知识库回答
2. 外部资料有更具体信息时可补充
3. 涉及数量统计（多少个/总数/共计/几条/几项）的问题，必须以检索到的表格资料顶部的「本表格共 N 条记录」或逐条计数为准，禁止凭记忆估算或编造数字
4. 只有当你确实引用了"外部检索资料"中的具体内容时，才在回答末尾列出参考文件名称，如"（参考文件：xxx、xxx）"；
   时间/日期/星期等问题请直接依据"当前时间"回答，不要列出参考文件；
   严禁把"当前时间""每日简报""学校内建知识库""系统实时注入时间"等内部来源当作参考文件列出
5. 都不足以回答时，请如实告知
6. 常识性问题可直接用自己的知识回答
7. 回答使用中文，结构清晰
8. 参考资料中标注的「上传时间」来自数据库 created_at，「索引完成时间」来自 indexed_at，
   均为 Asia/Shanghai 北京时间；回答文件上传/索引时间时只能引用这两个字段，
   不得把文件内容里的日期、freshness_date 或当前时间当作上传时间。
   若参考资料没有索引完成时间，必须说明“暂无索引完成时间/尚未完成索引”，禁止编造或拿上传时间代替。"""


# ------------------------------------------------------------------
# RAG 控制参数（可通过 settings 覆盖）
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# 设计说明：
# - TOP_K 初检取多些，给 Rerank 留挑选空间
# - SCORE_THRESHOLD 初检宽一些（0.35→0.30），避免漏召回；Rerank 精排后重新评估
RAG_TOP_K = 15
RAG_SCORE_THRESHOLD = 0.30      # 低于此分数不放入 LLM 上下文（Rerank 精排后重新评估）
RAG_REFERENCE_THRESHOLD = 0.65  # 低于此分数不作为引用展示
CONTEXT_MAX_CHARS = 8000

# ------------------------------------------------------------------
# 二次检索（检索失败/低置信时扩大范围重试一次）
# ------------------------------------------------------------------
# 首次检索为空、最高分过低，或只有一条且分数不高时，放宽硬过滤条件、
# 扩大召回数量并降低最低分阈值再检索一次；两次结果按现有分数合并去重。
RAG_RETRY_TOP_K = 30            # 重试时的召回数量（首次为 RAG_TOP_K）
RAG_RETRY_MIN_SCORE = 0.15      # 重试时允许进入上下文的最低分
RAG_RETRY_TRIGGER_SCORE = 0.50  # 首次最高分低于此值触发重试
RAG_RETRY_SINGLE_RESULT_SCORE = 0.65  # 只有一条结果且低于此值时视为“明显不足”
RAG_RETRY_MIN_RESULTS = 2       # 低于此结果数且分数不足时触发重试
RAG_RETRY_MAX_ATTEMPTS = 1      # 重试上限，禁止无限扩大检索
_RAG_RETRY_DROP_FILTERS = ('time_range', 'file_type', 'category_ids')


# ------------------------------------------------------------------
# 分角色严谨度（管理员低置信拒答 + 强制引用）
# ------------------------------------------------------------------
ADMIN_MIN_SCORE = 0.5  # 管理员最低可答分数阈值：知识库无命中或最高分低于此值拒答
ADMIN_ROLES = {'super_admin', 'admin', 'college_admin', 'dept_admin'}

# 普通用户联网兜底阈值：知识库最高分低于此值视为"低置信"（命中大概率是泛化内容），
# 触发联网搜索兜底，用真实网页结果压住幻觉（时效性/具体新闻类问题受益最大）。
WEB_SEARCH_MIN_SCORE = 0.5


def is_strict_role(role: str) -> bool:
    """管理员等需精准引用的角色返回 True，学生（user）返回 False。"""
    return role in ADMIN_ROLES


# ------------------------------------------------------------------
# Prompt 构造
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# 多轮对话摘要（超长历史时自动压缩）
# ------------------------------------------------------------------
MAX_HISTORY_ROUNDS = 3          # 超过 3 轮（6 条消息）触发摘要
MAX_SUMMARY_CHARS = 200         # 摘要最大字符数

_SUMMARIZE_PROMPT = """你是对话摘要助手。请将以下多轮对话历史压缩为一段简洁的摘要（{max_chars}字以内），
保留关键信息：用户的核心需求、已完成的任务、当前讨论的主题。

对话历史：
{history}

摘要："""


def _summarize_history(history: List[dict]) -> Optional[str]:
    """
    将多轮对话历史压缩为摘要（调用 LLM）。

    Args:
        history: 历史消息列表 [{'role': str, 'content': str}, ...]

    Returns:
        摘要文本，失败返回 None（调用方回退到截断逻辑）
    """
    lines = []
    for msg in history:
        role = '用户' if msg['role'] == 'user' else '助手'
        lines.append(f"{role}: {msg['content'][:500]}")
    history_text = '\n'.join(lines)

    prompt = _SUMMARIZE_PROMPT.format(
        max_chars=MAX_SUMMARY_CHARS,
        history=history_text,
    )
    messages = [{'role': 'user', 'content': prompt}]

    try:
        summary = llm_client.chat(messages, temperature=0.2, max_tokens=200)
        if summary and len(summary.strip()) > 10:
            logger.info('对话摘要生成成功: %d 字符', len(summary))
            return summary.strip()
    except Exception as e:
        logger.warning('对话摘要生成失败: %s', e)

    return None


def _format_upload_time(value) -> str:
    """把文档 created_at（UTC aware）格式化为北京时间字符串；空值/异常返回空串。

    统一委托 utils.timefmt，避免各模块各自 strftime(UTC) 造成时区错位。
    """
    return format_business_time(value, '%Y-%m-%d %H:%M')


def build_rag_prompt(
    question: str,
    chunks: List[dict],
    history: Optional[List[dict]] = None,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> List[dict]:
    """
    构造 RAG Prompt（支持多轮对话历史）。

    Args:
        question: 用户问题
        chunks: 检索结果 [{'content': str, 'doc_title': str, 'page': int, 'score': float}, ...]
        history: 历史对话 [{'role': 'user'|'assistant', 'content': str}, ...]（可选）
        max_chars: 参考资料总字符上限（穷举类问题由调用方放宽，避免名单被截断）

    Returns:
        messages 列表，可直接传给 LLM chat
    """
    ref_lines = []
    total_chars = 0
    # 按 doc_id 批量取上传/索引时间（一次 IN 查询），注入参考资料块，
    # 让 LLM 对"文件什么时候上传/索引完成"类问题有据可答，避免编造时间。
    doc_ids = [c.get('doc_id') for c in chunks if c.get('doc_id')]
    doc_times = {}
    if doc_ids:
        from apps.knowledge.models import KnowledgeDoc
        doc_times = {
            row['id']: row
            for row in KnowledgeDoc.objects.filter(id__in=doc_ids).values(
                'id', 'created_at', 'indexed_at', 'freshness_date',
            )
        }
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get('doc_title', '未知文档')
        page = chunk.get('page', 0)
        content = chunk.get('content', '')
        ref_block = f'### 参考资料{i}（来源：{title}'
        if page:
            ref_block += f' 第{page}页'
        doc_time = doc_times.get(chunk.get('doc_id'), {})
        upload_time = _format_upload_time(doc_time.get('created_at'))
        if upload_time:
            ref_block += f'，上传时间：{upload_time}'
        indexed_time = format_business_time(doc_time.get('indexed_at'), '%Y-%m-%d %H:%M')
        ref_block += f'，索引完成时间：{indexed_time or "暂无（尚未完成索引）"}'
        freshness = doc_time.get('freshness_date')
        if freshness:
            ref_block += f'，内容截止日期：{freshness.isoformat()}'
        ref_block += f'）\n{content}'
        total_chars += len(ref_block)
        if total_chars > max_chars:
            break
        ref_lines.append(ref_block)

    refs_text = '\n\n'.join(ref_lines) if ref_lines else '暂无相关参考资料。'
    from utils.context import build_system_context
    system_content = SYSTEM_PROMPT.format(
        knowlege=NEUSOFT_KNOWLEDGE,
        references=refs_text,
        context=build_system_context(),
    )

    messages = [{'role': 'system', 'content': system_content}]

    # 插入历史对话（多轮上下文）
    if history:
        threshold = getattr(
            settings, 'MAX_HISTORY_ROUNDS_BEFORE_SUMMARY', MAX_HISTORY_ROUNDS,
        )
        if len(history) > threshold * 2:
            # 超过阈值：生成摘要 + 保留最近 2 轮
            summary = _summarize_history(history)
            if summary:
                messages[0]['content'] += (
                    f'\n\n## 历史对话摘要\n{summary}\n'
                )
                recent_history = history[-4:]  # 最近 2 轮 = 4 条消息
            else:
                # 摘要失败，回退到原有截断逻辑
                recent_history = history[-6:]
        else:
            recent_history = history[-6:]

        for msg in recent_history:
            if msg['role'] in ('user', 'assistant'):
                messages.append({
                    'role': msg['role'],
                    'content': msg['content'][:2000],  # 每条历史截断
                })

    # 当前问题
    messages.append({'role': 'user', 'content': question})
    return messages


# ------------------------------------------------------------------
# 相关推荐
# ------------------------------------------------------------------
_SUGGEST_PROMPT = """你是成都东软学院智能问答系统的推荐助手。
你的任务是根据用户刚才的提问和AI的回答，生成3个追问建议，帮助用户深入了解相关信息。

每个建议需要同时给出两种文本：
1. "display"：引导式问句，用于前端展示，语气亲切自然、承接上文，例如"您查询了学校在校生人数，需要我为您分别整理各学院的学生分布情况吗？"
2. "question"：去掉引导套话后的干净问题，是用户点击后实际发送的提问，必须是一句话问句，不要带"您刚才问了""需要我为您""让我帮您"等引导/客套词

要求：
- display 与 question 表达同一个意思，但 question 更简短、可直接作为提问发送
- 覆盖不同维度：数据细分、横向对比、操作指南、政策解读、历史沿革等

用 JSON 数组输出，每个元素是 {"display": "...", "question": "..."}，不要额外文字。
示例输出：[{"display": "您查询了学校在校生人数，需要我为您分别整理各学院的学生分布情况吗？", "question": "各学院的学生人数分别是多少？"}, {"display": "想了解专任教师的具体构成吗？比如高级职称和硕博士比例？", "question": "专任教师中高级职称和硕博士各占多少？"}, {"display": "需要我帮您对比近几年在校生人数的变化趋势吗？", "question": "近几年在校生人数有什么变化趋势？"}]

用户问题：{question}
AI回答：{answer}
"""


def generate_suggested_questions(
    question: str,
    answer: str,
    max_questions: int = 3,
) -> List[dict]:
    """根据问答内容生成相关推荐问题（展示句 + 可直接发送的干净问句）。

    Args:
        question: 用户原始问题
        answer: AI 完整回答
        max_questions: 推荐问题数量（默认 3）

    Returns:
        [{'display': str, 'question': str}, ...]，生成失败返回空列表
        - display: 引导式问句（前端展示用）
        - question: 干净问句（用户点击后实际发送）
    """
    if not answer or not answer.strip():
        return []

    import json
    import re as _re

    # 用 replace 而非 str.format，避免回答/问题里出现 { } 时触发 KeyError/ValueError
    prompt = (
        _SUGGEST_PROMPT
        .replace('{question}', question[:300])
        .replace('{answer}', answer[:1000])
    )
    messages = [{'role': 'user', 'content': prompt}]

    result = llm_client.chat(messages, temperature=0.7, max_tokens=400)
    if not result:
        return []

    # 解析 JSON 数组
    parsed = None
    try:
        parsed = json.loads(result.strip())
    except (json.JSONDecodeError, TypeError):
        # 尝试从 markdown 代码块中提取
        match = _re.search(r'```(?:json)?\s*(\[.*?\])\s*```', result, _re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                parsed = None

    if not isinstance(parsed, list):
        return []

    # 归一化：兼容 LLM 偶尔返回纯字符串（旧格式），统一转为 {display, question}
    normalized = []
    for item in parsed:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({'display': text, 'question': text})
        elif isinstance(item, dict):
            display = str(item.get('display') or '').strip()
            question = str(item.get('question') or '').strip()
            if not question:
                question = display
            if not display:
                display = question
            if question:
                normalized.append({'display': display, 'question': question})
        if len(normalized) >= max_questions:
            break
    return normalized


# ------------------------------------------------------------------
# 查询词提取（元数据 / Keyword 表 / 分块检索共用）
# ------------------------------------------------------------------
_STOP_WORDS = {
    "什么", "怎么", "如何", "为什么", "吗", "呢", "吧", "嘛",
    "哪个", "哪些", "谁", "何时", "哪里", "是否", "有", "没有",
    "的", "了", "我", "你", "他", "她", "它", "们", "这个", "那个",
    "是什么", "请问", "关于",
}


def _extract_query_terms(question: str, max_terms: int = 8) -> List[str]:
    """从用户问题中提取用于数据库匹配的查询词。

    与传给向量检索的完整 question 不同，query_terms 用于
    icontains 匹配，须过滤停用词和单字，并对中英混合词做拆解。

    Returns:
        去重后的查询词列表（含同义词扩展），最多 max_terms 个。
    """
    import re

    if not question or not question.strip():
        return []

    terms: List[str] = []
    for word in re.findall(r'[一-鿿\w]+', question):
        word = word.lower()
        if word in _STOP_WORDS or len(word) < 2 or len(word) > 20:
            continue
        if word not in terms:
            terms.append(word)
        # 拆解混合词：如 "MySQL第二版" → "mysql" + "第二版"
        parts = re.findall(r'[a-z]+|[一-鿿]+', word)
        if len(parts) > 1:
            for part in parts:
                if (
                    len(part) >= 2
                    and part not in _STOP_WORDS
                    and part not in terms
                ):
                    terms.append(part)

    # jieba 分词补充（可缺失，缺失时正则词已够用）
    try:
        import jieba
        for token in jieba.cut(question):
            token = token.strip().lower()
            if (
                len(token) >= 2
                and len(token) <= 20
                and token not in _STOP_WORDS
                and token not in terms
            ):
                terms.append(token)
    except ImportError:
        pass

    from utils.synonym import expand_keywords
    terms = expand_keywords(terms)
    return terms[:max_terms]


def _select_relevant_chunk(doc, keywords: List[str]):
    """选择与查询词最相关的分块：相关分块优先，首块兜底。

    数据库初筛（icontains OR）后按命中次数排序，避免读取全部正文。
    """
    from django.db.models import Q
    from apps.knowledge.models import DocChunk

    chunks = DocChunk.objects.filter(doc=doc).order_by('chunk_index')
    if not keywords:
        return chunks.first()

    chunk_query = Q()
    for kw in keywords:
        chunk_query |= Q(content__icontains=kw)
    matching = list(chunks.filter(chunk_query)[:10])
    if not matching:
        return chunks.first()

    def _hit_rank(chunk):
        content = chunk.content.lower()
        return (
            -sum(content.count(kw.lower()) for kw in keywords),
            chunk.chunk_index,
        )

    matching.sort(key=_hit_rank)
    return matching[0]


def _build_doc_results(docs, keywords: List[str], score: float) -> List[dict]:
    """把文档 QuerySet 统一构造为检索结果（相关分块正文 + 来源元数据）。"""
    results = []
    for doc in docs:
        chunk = _select_relevant_chunk(doc, keywords)
        if chunk is not None:
            content = chunk.content
            page = chunk.page_start or 0
            chunk_index = chunk.chunk_index
            vector_id = chunk.vector_id
        else:
            content = f"{doc.title} {(doc.description or '')[:300]}".strip()
            page = 0
            chunk_index = None
            vector_id = ''
        results.append({
            'doc_id': doc.id,
            'doc_title': doc.title,
            'content': content,
            'score': score,
            'page': page,
            'chunk_index': chunk_index,
            'vector_id': vector_id,
            'source_type': doc.source_type,
            'source_url': doc.source_url,
            'file_name': doc.file_name,
            'oss_key': doc.oss_key,
        })
    return results


# ------------------------------------------------------------------
# 关键词搜索
# ------------------------------------------------------------------
def _keyword_search(
    question: str,
    top_k: int = 10,
    doc_ids: Optional[List[int]] = None,
    user_context: Optional[dict] = None,
) -> List[dict]:
    """在 KnowledgeDoc 元数据（标题/描述/文件名）中进行关键词匹配。"""
    keywords = _extract_query_terms(question)
    if not keywords:
        return []

    from django.db.models import Q

    kw_query = Q()
    for kw in keywords:
        kw_query |= Q(title__icontains=kw)
        kw_query |= Q(description__icontains=kw)
        kw_query |= Q(file_name__icontains=kw)

    # The same authorization scope is used by vector, keyword, and title search.
    qs = authorized_knowledge_docs(user_context).filter(
        kw_query, status='indexed',
    )
    if doc_ids is not None:
        qs = qs.filter(id__in=[doc_id for doc_id in doc_ids if doc_id > 0])

    results = []
    for doc in qs.values(
        'id', 'title', 'description', 'source_type', 'source_url',
        'file_name', 'oss_key',
    )[:top_k]:
        excerpt = (doc['description'] or '')[:300]
        results.append({
            'doc_id': doc['id'],
            'doc_title': doc['title'],
            'content': f"{doc['title']} {excerpt}".strip(),
            'score': 0.55,
            'page': 0,
            'source_type': doc['source_type'],
            'source_url': doc['source_url'],
            'file_name': doc['file_name'],
            'oss_key': doc['oss_key'],
        })
    return results


def _keyword_table_search(
    keywords: List[str],
    user_context: Optional[dict] = None,
    top_k: int = 5,
    doc_ids: Optional[List[int]] = None,
) -> List[dict]:
    """Keyword 表检索通道：按人工标注的关键词反查文档。

    先查 Keyword.phrase 得到 doc_id 集合，再统一经过
    authorized_knowledge_docs() 权限过滤和 status='indexed' 限制，
    不能把 Keyword 查询结果直接返回。
    """
    if not keywords:
        return []

    from django.db.models import Q
    from apps.knowledge.models import Keyword

    keyword_query = Q()
    for kw in keywords:
        keyword_query |= Q(phrase__icontains=kw)

    doc_id_qs = Keyword.objects.filter(keyword_query).values('doc_id')
    docs = authorized_knowledge_docs(user_context).filter(
        id__in=doc_id_qs, status='indexed',
    )
    if doc_ids is not None:
        docs = docs.filter(id__in=[d for d in doc_ids if d > 0])
    docs = docs.order_by('-created_at')[:top_k]

    return _build_doc_results(docs, keywords, score=0.6)


def _chunk_content_search(
    keywords: List[str],
    user_context: Optional[dict] = None,
    top_k: int = 5,
    doc_ids: Optional[List[int]] = None,
) -> List[dict]:
    """正文分块检索通道：按 DocChunk.content 匹配查询词。

    解决标题/文件名/Keyword 表都不含查询词、但正文提到的情况。
    必须先限制在授权且已索引的文档范围内，防止越权泄露。
    """
    if not keywords:
        return []

    from django.db.models import Q
    from apps.knowledge.models import DocChunk

    allowed_docs = authorized_knowledge_docs(user_context).filter(
        status='indexed',
    )
    if doc_ids is not None:
        allowed_docs = allowed_docs.filter(id__in=[d for d in doc_ids if d > 0])

    chunk_query = Q()
    for kw in keywords:
        chunk_query |= Q(content__icontains=kw)

    chunks = (
        DocChunk.objects
        .filter(doc__in=allowed_docs)
        .filter(chunk_query)
        .select_related('doc')
        .order_by('doc_id', 'chunk_index')[:top_k * 3]
    )

    results = []
    for chunk in chunks:
        content_lower = chunk.content.lower()
        hit_count = sum(content_lower.count(kw.lower()) for kw in keywords)
        results.append({
            'doc_id': chunk.doc_id,
            'doc_title': chunk.doc.title,
            'content': chunk.content,
            'score': min(0.6, 0.4 + 0.05 * hit_count),
            'page': chunk.page_start or 0,
            'chunk_index': chunk.chunk_index,
            'vector_id': chunk.vector_id,
            'source_type': chunk.doc.source_type,
            'source_url': chunk.doc.source_url,
            'file_name': chunk.doc.file_name,
            'oss_key': chunk.doc.oss_key,
        })

    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:top_k]


def _keywords_boost_from_filters(filters: dict) -> dict:
    """
    从 parsed_filters 中提取高权重词，用于 RAG 召回加成。

    Args:
        filters: parse() 输出的过滤条件

    Returns:
        关键词 → 权重映射
    """
    boost = {}
    if term := filters.get('course_term'):
        boost[term] = 10
    return boost


# ------------------------------------------------------------------
# 合并重排序
# ------------------------------------------------------------------
def _merge_and_rerank(
    vector_hits: List[dict],
    keyword_hits: List[dict],
    vector_weight: float = 0.7,
    keywords_boost: Optional[dict] = None,
) -> List[dict]:
    """
    合并两路结果并重排序。
    向量得分 × 0.7 + 关键词得分 × 0.3 + 关键词表权重加成。

    Args:
        vector_hits: Milvus 命中
        keyword_hits: MySQL 关键词命中
        vector_weight: 向量权重（默认 0.7）
        keywords_boost: 关键词→权重映射，例：{'操作系统': 10}
    """
    keywords_boost = keywords_boost or {}
    merged = {}

    # 关键词通道（元数据/关键词表/分块）按文档聚合：同文档多 chunk 逐条累加
    # 会造成分数虚高（如同一文档 10 个 chunk 命中叠加到 7.0），排序失真。
    # 每文档只保留最高分一条作为文档级证据；同文档多块由向量通道提供。
    best_by_doc = {}
    for hit in keyword_hits:
        did = hit.get('doc_id')
        if did not in best_by_doc or hit['score'] > best_by_doc[did]['score']:
            best_by_doc[did] = hit
    keyword_hits = sorted(
        best_by_doc.values(), key=lambda h: h['score'], reverse=True,
    )

    # 无向量命中时关键词分数不衰减：vector_weight 缩放本意是双通道
    # 量纲对齐，向量为空时再乘 (1-0.7) 会把 0.55 压到 0.165，
    # 被 min_score 全部过滤掉。
    kw_weight = (1 - vector_weight) if vector_hits else 1.0

    for hit in vector_hits:
        key = hit.get('vector_id') or f"v_{hit.get('doc_id')}_{hit.get('chunk_index')}"
        merged[key] = {
            'doc_id': hit.get('doc_id'),
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id'),
            'content': hit.get('chunk_text', ''),
            'page': hit.get('page_number', 0),
            'doc_title': hit.get('doc_title', ''),
            'source_type': hit.get('source_type', ''),
            'source_url': hit.get('source_url', ''),
            'file_name': hit.get('file_name', ''),
            'oss_key': hit.get('oss_key', ''),
            'score': hit['score'] * vector_weight,
        }

    for hit in keyword_hits:
        # Merge keyword evidence into the best existing hit for the same document;
        # vector chunks remain separate so useful passages are not discarded.
        existing = next(
            (item for item in merged.values() if item.get('doc_id') == hit.get('doc_id')),
            None,
        )
        if existing:
            existing['score'] += hit['score'] * kw_weight
            # 有正文分块命中的结果优先于"标题+描述摘录"作为 LLM 上下文
            if (
                hit.get('chunk_index') is not None
                and existing.get('chunk_index') is None
            ):
                existing['content'] = hit.get('content', '')
                existing['page'] = hit.get('page', 0)
                existing['chunk_index'] = hit.get('chunk_index')
                existing['vector_id'] = hit.get('vector_id', '')
            for field in ('doc_title', 'source_type', 'source_url', 'file_name', 'oss_key'):
                if hit.get(field) and not existing.get(field):
                    existing[field] = hit[field]
        else:
            key = f"kw_{hit.get('doc_id')}"
            merged[key] = {
                'doc_id': hit.get('doc_id'),
                'doc_title': hit.get('doc_title', ''),
                'content': hit.get('content', ''),
                'page': hit.get('page', 0),
                'vector_id': '',
                'score': hit['score'] * kw_weight,
                'source_type': hit.get('source_type', ''),
                'source_url': hit.get('source_url', ''),
                'file_name': hit.get('file_name', ''),
                'oss_key': hit.get('oss_key', ''),
            }

    # 关键词权重加成
    if keywords_boost:
        for hit in merged.values():
            content = (hit.get('content') or '').lower()
            extra = 0
            for word, weight in keywords_boost.items():
                if word.lower() in content:
                    extra += weight / 50.0
            hit['score'] += extra

    return sorted(merged.values(), key=lambda x: x['score'], reverse=True)


# ------------------------------------------------------------------
# 文档授权范围（所有检索路径共用）
# ------------------------------------------------------------------
def authorized_knowledge_docs(user_context: Optional[dict] = None):
    """返回当前用户可检索的 KnowledgeDoc QuerySet。

    授权逻辑必须在召回和引用签名前保持一致。超管可查看全部文档，
    其他角色仅可查看公开的学校/本部门或本人上传文档。
    """
    from django.db.models import Q
    from apps.knowledge.models import KnowledgeDoc

    user_context = user_context or {}
    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')
    college_id = user_context.get('college_id')
    department_id = user_context.get('department_id')

    qs = KnowledgeDoc.objects.all()
    if role == 'super_admin':
        return qs

    qs = qs.filter(visibility='public')
    school_level = Q(college_id__isnull=True, department_id__isnull=True)
    if role in ('admin', 'college_admin'):
        return qs.filter(school_level | Q(college_id=college_id) | Q(uploader_id=user_id))
    if role == 'dept_admin':
        return qs.filter(school_level | Q(department_id=department_id) | Q(uploader_id=user_id))

    conditions = school_level
    if college_id:
        conditions |= Q(college_id=college_id)
    if department_id:
        conditions |= Q(department_id=department_id)
    if user_id:
        conditions |= Q(uploader_id=user_id)
    return qs.filter(conditions)


# ------------------------------------------------------------------
# 穷举类问题扩展（"列出名单/都有谁"类查询拉取目标文档全部分块）
# ------------------------------------------------------------------
# 背景：名单类查询（如"15级优秀毕业生有谁"）的名单往往跨十几个分块，
# 常规检索按语义相似度取 top-5，5 个槽位还会被同标题的多个年份文档平摊
# （2010-2015 届《优秀毕业生》各占 1 块），LLM 只看到目标文档 1/13 的
# 分块，回答必然残缺。解法：识别穷举意图后，把目标文档的全部 DocChunk
# 按 chunk_index 顺序整体注入上下文，并放宽 build_rag_prompt 的字符上限。
ENUM_CONTEXT_MAX_CHARS = 24000  # 穷举类问题的参考资料字符上限（13 块 × 500 字 ≈ 6.5K，留足余量）

_ENUM_INTENT_KEYWORDS = (
    '列出', '列举', '名单', '所有', '全部', '有谁', '都有谁',
    '哪些人', '哪些同学', '人员', '分别是',
)


def is_enumeration_question(question: str) -> bool:
    """检测穷举类意图：要求完整罗列条目（名单/所有/有谁…）。"""
    q = (question or '').strip()
    return any(k in q for k in _ENUM_INTENT_KEYWORDS)


_AGGREGATION_INTENT_KEYWORDS = (
    '多少个', '多少项', '多少条', '多少名', '多少位', '多少篇',
    '一共', '共计', '总数', '总数量', '数量',
    '有几项', '有几条', '有几个', '有几名',
    '不重复', '去重', '统计', '汇总', '合计',
)


def is_aggregation_question(question: str) -> bool:
    """检测聚合/计数类意图：需要统计记录条数或汇总字段。

    计数类问题与穷举类问题一样，答案依赖全量记录而非语义 top-K 碎片；
    因此命中后走 try_expand_enumeration 的全量注入通道。
    """
    q = (question or '').strip()
    return any(k in q for k in _AGGREGATION_INTENT_KEYWORDS)


def _extract_query_years(question: str) -> List[str]:
    """提取问题中的年份（"15级/2015届/2015年"→2015），用于精确定位目标文档。"""
    import re as _re

    years: List[str] = []
    for m in _re.finditer(r'(\d{2,4})\s*[届级]', question or ''):
        y = m.group(1)
        y = y if len(y) == 4 else f'20{y}'
        if y not in years:
            years.append(y)
    for m in _re.finditer(r'(?:19|20)\d{2}', question or ''):
        if m.group(0) not in years:
            years.append(m.group(0))
    return years


def _pick_enum_target_doc(
    results: List[dict],
    question: str,
    score_gap: float,
) -> Optional[dict]:
    """从检索结果中定位穷举目标文档。

    - 问题含年份：标题/文件名含该年份的命中中取分数最高者；年份找不到
      对应文档时返回 None——宁可不扩展，也不能拉错年份给"完整的错误名单"。
    - 无年份：仅当结果明显集中于单一文档（top1 领先 top2 ≥ score_gap）才扩展，
      避免在多个候选文档间武断拉满一个。
    """
    years = _extract_query_years(question)
    if years:
        matched = [
            r for r in results
            if any(
                y in f"{r.get('doc_title', '')}{r.get('file_name', '')}"
                for y in years
            )
        ]
        return max(matched, key=lambda r: r.get('score', 0)) if matched else None

    if len(results) == 1:
        return results[0]
    if (
        len(results) >= 2
        and results[0].get('score', 0) - results[1].get('score', 0) >= score_gap
    ):
        return results[0]
    return None


def try_expand_enumeration(
    question: str,
    results: List[dict],
    user_context: Optional[dict] = None,
    score_gap: float = 0.05,
) -> Tuple[List[dict], Optional[str]]:
    """穷举类问题：把目标文档的全部分块按序注入检索结果。

    必须复用 authorized_knowledge_docs() 权限过滤 + status='indexed'
    限制，扩展逻辑不得成为越权读取通道。

    Returns:
        (新结果列表, 目标文档标题或 None)。目标文档分块按 chunk_index
        升序排最前，其余原始命中（剔除目标文档重复块）按原顺序跟在后面。
        未触发扩展时原样返回 (results, None)。
    """
    if not results or not (
        is_enumeration_question(question) or is_aggregation_question(question)
    ):
        return results, None

    target = _pick_enum_target_doc(results, question, score_gap)
    if not target or not target.get('doc_id'):
        return results, None

    doc_id = target['doc_id']
    if not authorized_knowledge_docs(user_context).filter(
        id=doc_id, status='indexed',
    ).exists():
        return results, None

    from apps.knowledge.models import DocChunk
    chunks = list(DocChunk.objects.filter(doc_id=doc_id).order_by('chunk_index'))
    # 单块文档不存在"名单跨块截断"问题，无需扩展
    if len(chunks) <= 1:
        return results, None

    expanded = [
        {
            'doc_id': doc_id,
            'doc_title': target.get('doc_title', ''),
            'content': c.content,
            'score': target.get('score', 0.0),
            'page': c.page_start or 0,
            'chunk_index': c.chunk_index,
            'vector_id': c.vector_id,
            'source_type': target.get('source_type', ''),
            'source_url': target.get('source_url', ''),
            'file_name': target.get('file_name', ''),
            'oss_key': target.get('oss_key', ''),
        }
        for c in chunks
    ]
    others = [r for r in results if r.get('doc_id') != doc_id]
    return expanded + others, target.get('doc_title', '')


# ------------------------------------------------------------------
# 硬过滤（SQL WHERE 缩范围）
# ------------------------------------------------------------------
def _apply_hard_filters(
    filters: dict,
    user_context: dict,
    top_k: int = 500,
) -> List[int]:
    """对 KnowledgeDoc 执行 SQL 硬过滤并返回候选文档 ID。"""
    from apps.knowledge.models import KnowledgeDoc

    time_range = filters.get('time_range')
    qs = authorized_knowledge_docs(user_context).filter(status='indexed')

    # 不再默认只检索近一年；只有用户明确给出时间范围时才过滤。
    # time_range 可能来自意图解析的自由输入，非字符串时直接忽略，避免 re.match 抛 TypeError。
    if isinstance(time_range, str) and time_range:
        import re
        # 用业务时区的左闭右开区间，而不是 __year/__month：
        # 后者依赖数据库时区表，且跨时区边界时会把 UTC 年份/月份算错。
        try:
            if re.match(r'^\d{4}$', time_range):
                start, end = year_bounds(int(time_range))
                qs = qs.filter(created_at__gte=start, created_at__lt=end)
            elif re.match(r'^\d{4}-\d{2}$', time_range):
                year, month = (int(part) for part in time_range.split('-'))
                start, end = month_bounds(year, month)
                qs = qs.filter(created_at__gte=start, created_at__lt=end)
        except ValueError:
            logger.warning('忽略非法 time_range: %r', time_range)

    if (cid := filters.get('college_id')):
        qs = qs.filter(college_id=cid)
    if (did := filters.get('discipline_id')):
        qs = qs.filter(discipline_id=did)
    if (cat_ids := filters.get('category_ids')):
        qs = qs.filter(category_id__in=cat_ids)
    if (ft := filters.get('file_type')):
        qs = qs.filter(file_type=ft)

    return list(qs.values_list('id', flat=True)[:top_k])


# ------------------------------------------------------------------
# 权限校验（过滤 Top-K 后再过滤 visibility）
# ------------------------------------------------------------------
def _filter_by_permission(
    hits: List[dict],
    user_context: dict,
) -> List[dict]:
    """
    在 Top-K 命中里剥离用户不可见的 doc。
    """
    if not hits:
        return []

    doc_ids = list({h.get('doc_id') for h in hits if h.get('doc_id')})
    if not doc_ids:
        return []
    allowed_ids = set(
        authorized_knowledge_docs(user_context)
        .filter(id__in=doc_ids, status='indexed')
        .values_list('id', flat=True)
    )
    return [h for h in hits if h.get('doc_id') in allowed_ids][:RAG_TOP_K]


# ------------------------------------------------------------------
# 加载 doc 元信息


# ------------------------------------------------------------------
# 加载 doc 元信息
# ------------------------------------------------------------------
def _enrich_with_doc_meta(hits: List[dict], min_score: float) -> List[dict]:
    """
    关键词命中返回 content 字段（标题 + 描述拼接），用于补充检索结果。
    """
    from apps.knowledge.models import KnowledgeDoc

    doc_ids = list({int(h['doc_id']) for h in hits if h.get('doc_id')})
    doc_title_map = {
        row['id']: row for row in KnowledgeDoc.objects.filter(
            id__in=doc_ids, status='indexed',
        ).values(
            'id', 'title', 'source_type', 'source_url', 'file_name', 'oss_key',
            'created_at', 'indexed_at', 'freshness_date',
        )
    }

    results = []
    for hit in hits:
        if hit['score'] < min_score:
            continue
        if not hit.get('content'):
            continue
        did = int(hit['doc_id'])
        meta = doc_title_map.get(did, {})
        results.append({
            'doc_id': did,
            'doc_title': meta.get('title') or hit.get('doc_title', '未知文档'),
            'content': hit['content'],
            'score': round(hit['score'], 4),
            'page': hit.get('page', 0),
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id', ''),
            'source_type': meta.get('source_type') or hit.get('source_type', ''),
            'source_url': meta.get('source_url') or hit.get('source_url', ''),
            'file_name': meta.get('file_name') or hit.get('file_name', ''),
            'oss_key': meta.get('oss_key') or hit.get('oss_key', ''),
            'created_at': to_iso(meta.get('created_at')),
            'created_at_display': format_business_time(meta.get('created_at')),
            'indexed_at': to_iso(meta.get('indexed_at')),
            'indexed_at_display': format_business_time(meta.get('indexed_at')),
            'freshness_date': (
                meta['freshness_date'].isoformat() if meta.get('freshness_date') else None
            ),
        })
        if len(results) >= 5:
            break

    return results


# ------------------------------------------------------------------
# 兜底：按标题/描述关键词搜索（向量检索无结果时降级）
# ------------------------------------------------------------------
def _fallback_title_search(
    question: str,
    user_context: dict,
    top_k: int = 5,
) -> List[dict]:
    """当向量检索无结果时，从 KnowledgeDoc 标题/描述/文件名中按关键词匹配。

    Returns:
        [{'doc_id': int, 'doc_title': str, 'content': str, 'score': float, 'page': int}, ...]
    """
    from django.db.models import Q

    # 提取关键词：去疑问词，取有意义的词（元数据/分块检索共用同一提取逻辑）
    keywords = _extract_query_terms(question, max_terms=5)
    if not keywords:
        return []

    # 按关键词匹配标题/描述/文件名
    kw_query = Q()
    for kw in keywords:
        kw_query |= Q(title__icontains=kw)
        kw_query |= Q(description__icontains=kw)
        kw_query |= Q(file_name__icontains=kw)

    # Apply the same authorization scope as every other retrieval path.
    docs = authorized_knowledge_docs(user_context).filter(kw_query, status='indexed')
    docs = docs.order_by('-created_at')[:top_k]

    # 取每篇文档的最相关 chunk 作为内容预览（首块兜底）
    return _build_doc_results(docs, keywords, score=0.5)


def find_pending_index_docs(
    question: str,
    user_context: Optional[dict] = None,
    top_k: int = 3,
) -> List[dict]:
    """检索无结果时的诊断查询：查找匹配查询词但尚未完成索引的文档。

    用于向前端明确提示"知识库存在相关文件但暂不可检索"，
    与"没有匹配文件"区分开。仅返回用户有权访问的文档，
    未索引文档不得作为正常来源传给 LLM。

    Returns:
        [{'doc_id': int, 'doc_title': str, 'status': str}, ...]
    """
    keywords = _extract_query_terms(question, max_terms=5)
    if not keywords:
        return []

    from django.db.models import Q

    kw_query = Q()
    for kw in keywords:
        kw_query |= Q(title__icontains=kw)
        kw_query |= Q(description__icontains=kw)
        kw_query |= Q(file_name__icontains=kw)

    docs = (
        authorized_knowledge_docs(user_context)
        .filter(kw_query)
        .exclude(status='indexed')
        .order_by('-created_at')[:top_k]
    )
    return [
        {
            'doc_id': doc.id,
            'doc_title': doc.title,
            'status': doc.status,
            'status_display': doc.get_status_display(),
            'created_at': to_iso(doc.created_at),
            'created_at_display': format_business_time(doc.created_at),
            'indexed_at': to_iso(doc.indexed_at),
            'indexed_at_display': format_business_time(doc.indexed_at),
        }
        for doc in docs
    ]


# ------------------------------------------------------------------
# 图片检索（AI 问答内联展示图片）
# ------------------------------------------------------------------
IMAGE_INTENT_KEYWORDS = ('图片', '照片', '海报', 'logo', '图标', '截图', '设计图', '示意图')


def _detect_image_intent(question: str) -> bool:
    """检测用户是否明确想找图片（用于放宽图片召回数量 + 回答中提及图片）。"""
    q = (question or '').lower()
    return any(k in q for k in IMAGE_INTENT_KEYWORDS)


def retrieve_images(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = 4,
) -> List[dict]:
    """检索与问题相关的图片文档，用于 AI 问答流内联展示。

    与文本 RAG 的 retrieve() 相互独立：图片没有文本分块、不参与向量召回，
    但图片入库时已由视觉模型生成 title/description，此处按标题/描述关键词匹配。

    注意：**不按 status='indexed' 过滤**——图片无文本可索引，index_document_task
    会把图片置为 'failed'（或误把二进制当文本索引），因此仅按
    file_type='image' + visibility='public' + 权限过滤，功能自洽。

    Returns:
        [{'doc_id','doc_title','description','oss_key','file_name','score'}, ...]
    """
    user_context = user_context or {'role': 'user'}
    parsed_filters = parsed_filters or {}

    # 关键词提取（复用 _keyword_search 的 jieba + 同义词扩展）
    try:
        import jieba
        keywords = list(jieba.cut(question))[:10]
    except ImportError:
        keywords = question.split()[:10]

    from utils.synonym import expand_keywords
    keywords = expand_keywords(keywords)

    valid_keywords = [k for k in keywords if len(k) >= 2]
    if not valid_keywords:
        return []

    from django.db.models import Q
    from apps.knowledge.models import KnowledgeDoc
    from apps.knowledge.serializers import IMAGE_FILE_TYPES

    # 兼容存量数据：file_type 可能是类别 'image'，也可能是历史存的原始扩展名（jpg/png 等）
    image_types = ['image'] + sorted(IMAGE_FILE_TYPES)

    # 图片没有文本分块、不参与向量索引，index_document_task 会将其置为 failed
    # （见函数 docstring），故不按 status 过滤，仅按 file_type + 权限过滤，
    # 靠 title/description 关键词匹配召回。
    qs = authorized_knowledge_docs(user_context).filter(
        file_type__in=image_types,
    )

    # 权限过滤后的基准集合（不含关键词/学科/分类筛选），供图片意图兜底使用
    base_qs = qs

    # 关键词匹配标题 / 描述
    kw_query = Q()
    for kw in valid_keywords:
        kw_query |= Q(title__icontains=kw)
        kw_query |= Q(description__icontains=kw)
    qs = qs.filter(kw_query)

    # 学科 / 学院 / 分类筛选（与检索一致；不应用 file_type，已强制 image）
    if (cid := parsed_filters.get('college_id')):
        qs = qs.filter(college_id=cid)
    if (did := parsed_filters.get('discipline_id')):
        qs = qs.filter(discipline_id=did)
    if (cat_ids := parsed_filters.get('category_ids')):
        qs = qs.filter(category_id__in=cat_ids)

    results = []
    for d in qs.values('id', 'title', 'description', 'oss_key', 'file_name')[:top_k]:
        results.append({
            'doc_id': d['id'],
            'doc_title': d['title'],
            'description': (d['description'] or '')[:200],
            'oss_key': d['oss_key'],
            'file_name': d['file_name'],
            'score': 0.5,
        })

    # 图片意图但关键词/筛选匹配不到：兜底返回最近上传的公开图片，避免"找图"问题返回空
    if not results and _detect_image_intent(question):
        for d in base_qs.order_by('-created_at').values('id', 'title', 'description', 'oss_key', 'file_name')[:top_k]:
            results.append({
                'doc_id': d['id'],
                'doc_title': d['title'],
                'description': (d['description'] or '')[:200],
                'oss_key': d['oss_key'],
                'file_name': d['file_name'],
                'score': 0.3,
            })

    return results


# ------------------------------------------------------------------
# 检索主入口（升级版）
# ------------------------------------------------------------------
def _retrieval_top_score(results: List[dict]) -> float:
    """返回结果中的最高分；空结果返回 0。"""
    return max((r.get('score', 0) or 0) for r in results) if results else 0.0


def _should_retry_retrieval(results: List[dict]) -> Tuple[bool, str]:
    """判断首次检索是否需要扩大范围重试。

    - 空结果：必然重试；
    - 最高分低于 RAG_RETRY_TRIGGER_SCORE：视为低置信；
    - 只有一条且分数低于 RAG_RETRY_SINGLE_RESULT_SCORE：视为明显不足。
    """
    if not results:
        return True, 'empty'
    top_score = _retrieval_top_score(results)
    if top_score < RAG_RETRY_TRIGGER_SCORE:
        return True, f'low_top_score={top_score:.3f}'
    if (
        len(results) < RAG_RETRY_MIN_RESULTS
        and top_score < RAG_RETRY_SINGLE_RESULT_SCORE
    ):
        return True, f'few_results={len(results)},top_score={top_score:.3f}'
    return False, ''


def _widen_retrieval_filters(parsed_filters: Optional[dict]) -> dict:
    """重试时放宽可能误杀候选的硬过滤条件。

    只去掉时间、文件类型、分类三个限制；学院/学科等权限相关范围保留，
    最终仍由 authorized_knowledge_docs() 做授权过滤。
    """
    return {
        key: value for key, value in (parsed_filters or {}).items()
        if key not in _RAG_RETRY_DROP_FILTERS
    }


def _merge_retry_results(first: List[dict], second: List[dict]) -> List[dict]:
    """合并首次与重试结果：按 (doc_id, chunk_index) 去重，保留高分项。"""
    merged: Dict[tuple, dict] = {}
    for item in list(first or []) + list(second or []):
        key = (item.get('doc_id'), item.get('chunk_index'))
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            continue
        item_score = item.get('score', 0) or 0
        existing_score = existing.get('score', 0) or 0
        # 同一分块只保留高分项；文档级结果与分块级结果 key 不同，各自保留
        if item_score > existing_score:
            merged[key] = item
    return sorted(
        merged.values(), key=lambda r: (r.get('score', 0) or 0), reverse=True,
    )


def _retrieve_pipeline(
    question: str,
    user_context: dict,
    parsed_filters: Optional[dict],
    top_k: int,
    min_score: float,
    rewrite_questions: Optional[List[str]] = None,
) -> List[dict]:
    """执行一次完整检索管道（硬过滤 → 多路召回 → 合并精排 → 权限校验）。

    retrieve() 与 retrieve_wider() 共用此管道，避免二次检索重复实现一套逻辑。
    """
    parsed_filters = dict(parsed_filters or {})

    # 检测用户是否需要历史数据（含"历年/历史/全部"等关键词则不限时间）
    parsed_filters['need_history'] = _detect_history_intent(question)

    # 0. Query 改写：口语化提问 → 多个检索词（多路检索后合并去重）
    if rewrite_questions is None:
        from utils.query_rewriter import rewrite_query
        rewrite_questions = rewrite_query(question)
        if len(rewrite_questions) > 1:
            logger.debug('Query 改写: %s -> %s', question[:30], rewrite_questions)

    # 1. SQL 硬过滤 → 候选 ID 列表
    candidate_ids = _apply_hard_filters(parsed_filters, user_context, top_k=500)

    # 1b. 时间条件清零时降级去掉时间重试：
    #     "15级/2015届"指内容的届别年份，而 time_range 过滤的是文档上传
    #     时间（created_at）。意图解析把届别映射成 time_range 会误杀全部
    #     候选（历史届别文档都是近期上传的），宁可放宽时间也不能零召回。
    if not candidate_ids and parsed_filters.get('time_range'):
        stripped = {k: v for k, v in parsed_filters.items() if k != 'time_range'}
        candidate_ids = _apply_hard_filters(stripped, user_context, top_k=500)
        if candidate_ids:
            logger.info(
                f'time_range={parsed_filters["time_range"]!r} 清零候选，'
                f'已降级去掉时间条件重试: 候选 {len(candidate_ids)} 个'
            )

    if not candidate_ids:
        logger.info(f'硬过滤后无候选，尝试标题降级搜索: question={question[:50]}')
        title_matches = _fallback_title_search(question, user_context, top_k=5)
        if title_matches:
            logger.info(f'标题降级搜索命中 {len(title_matches)} 条')
            return title_matches
        return []

    logger.debug(f'硬过滤候选数: {len(candidate_ids)}, filters={parsed_filters}')

    # 2. 双路召回（仅在候选范围内检索，对每个改写版本都检索）
    vector_hits = []
    for q in rewrite_questions:
        q_emb = _cached_embed(q)
        if q_emb:
            vector_hits.extend(milvus_client.search(
                q_emb, top_k=top_k, filter_ids=candidate_ids,
            ))
    # 去重：按 vector_id 或 (doc_id, chunk_index) 去重，保留同文档的多块
    # 原来按 (doc_id, page) 去重会导致每篇文档只保留 1 个块（知识文档 page 恒为 0）
    seen = {}
    for h in vector_hits:
        # 优先用 vector_id（唯一标识）
        key = h.get('vector_id') or (h.get('doc_id'), h.get('chunk_index'))
        if key not in seen or h.get('score', 0) > seen[key].get('score', 0):
            seen[key] = h
    vector_hits = list(seen.values())

    keyword_hits = _keyword_search(
        question, top_k=top_k, doc_ids=candidate_ids,
        user_context=user_context,
    )

    # 2b. 关键词表通道 + 正文分块通道（与元数据检索共用统一查询词，
    #     全部经过 authorized_knowledge_docs() 权限过滤）
    query_terms = _extract_query_terms(question)
    keyword_table_hits = _keyword_table_search(
        query_terms, user_context, top_k=top_k, doc_ids=candidate_ids,
    )
    chunk_hits = _chunk_content_search(
        query_terms, user_context, top_k=top_k, doc_ids=candidate_ids,
    )

    # 3. 合并 + 关键词权重加成
    merged = _merge_and_rerank(
        vector_hits,
        keyword_hits + keyword_table_hits + chunk_hits,
        keywords_boost=_keywords_boost_from_filters(parsed_filters),
    )

    # 3.5 Cross-Encoder 精排（候选 >=3 即触发）
    try:
        from utils.reranker import CROSS_ENCODER_TOP_K_OUTPUT, rerank_with_cross_encoder
        if len(merged) >= 3:
            merged = rerank_with_cross_encoder(question, merged)
            logger.debug('Cross-Encoder 重排序后: %d 条', len(merged))
    except ImportError:
        pass  # reranker 模块不存在，静默跳过

    # 4. 加载 doc 元信息
    enriched = _enrich_with_doc_meta(merged, min_score)

    # 5. 权限校验
    results = _filter_by_permission(enriched, user_context)

    # 5.5 检索质量监控日志（学校投入使用后用于诊断与告警）
    # 向量命中率 = 向量命中的去重文档数 / 硬过滤候选数，反映向量通道覆盖率；
    # 持续偏低说明向量库损坏/维度不匹配/Embedding 失效（曾因中文路径导致全失效）。
    vector_hit_docs = len({h.get('doc_id') for h in vector_hits if h.get('doc_id')})
    vector_hit_rate = (vector_hit_docs / len(candidate_ids)) if candidate_ids else 0.0
    if results:
        top3_scores = [round(r.get('score', 0), 3) for r in results[:3]]
        logger.info(
            '[RAG监控] 候选=%d 向量=%d 向量命中率=%.3f 关键词=%d 关键词表=%d 分块=%d Rerank后Top3=%s 返回=%d',
            len(candidate_ids), len(vector_hits), vector_hit_rate,
            len(keyword_hits), len(keyword_table_hits), len(chunk_hits),
            top3_scores, len(results),
        )
    else:
        logger.warning(
            '[RAG监控] 零召回 q=%s 候选=%d 向量=%d 向量命中率=%.3f 关键词=%d 关键词表=%d 分块=%d',
            question[:50], len(candidate_ids),
            len(vector_hits), vector_hit_rate, len(keyword_hits),
            len(keyword_table_hits), len(chunk_hits),
        )

    # 6. 兜底：向量检索无结果时，按标题/描述关键词搜索
    if not results:
        title_matches = _fallback_title_search(question, user_context, top_k=5)
        if title_matches:
            logger.info(f'向量检索无结果，标题降级搜索命中 {len(title_matches)} 条')
            return title_matches

    return results


def retrieve_wider(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = RAG_RETRY_TOP_K,
    min_score: float = RAG_RETRY_MIN_SCORE,
    rewrite_questions: Optional[List[str]] = None,
) -> List[dict]:
    """扩大范围检索：放宽时间/类型/分类过滤，扩大召回并降低最低分。

    供 retrieve() 在首次结果为空或低置信时调用一次；也可单独用于诊断。
    不改动向量库与数据结构。
    """
    if _is_time_question(question):
        return []
    return _retrieve_pipeline(
        question=question,
        user_context=user_context or {'role': 'user'},
        parsed_filters=_widen_retrieval_filters(parsed_filters),
        top_k=top_k,
        min_score=min_score,
        rewrite_questions=rewrite_questions,
    )


def retrieve(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = RAG_TOP_K,
    min_score: float = RAG_SCORE_THRESHOLD,
) -> List[dict]:
    """
    检索管道（三层检索升级版）：硬过滤 → 双路召回 → 关键词权重 → 权限校验。

    首次结果为空或低置信时，自动扩大范围重试一次（RAG_RETRY_MAX_ATTEMPTS），
    两次结果按现有分数合并去重后返回。

    Args:
        question: 用户问题
        user_context: 用户上下文 {'user_id': int, 'role': str, 'college_id': int}
        parsed_filters: parse() 输出的过滤条件
        top_k: Milvus 召回数量
        min_score: 最低分阈值（低于此分数不返回）

    Returns:
        [{'doc_id': int, 'doc_title': str, 'content': str, 'score': float, 'page': int}, ...]
    """
    user_context = user_context or {'role': 'user'}
    parsed_filters = dict(parsed_filters or {})

    # 纯时间/日期问题直接跳过检索（由系统注入的「当前时间」回答），
    # 避免命中无关文档、污染参考答案。
    if _is_time_question(question):
        logger.info('纯时间问题，跳过 RAG 检索: %s', question[:30])
        return []

    # Query 改写只做一次，首次与重试共用，避免重复调用改写模型。
    from utils.query_rewriter import rewrite_query
    rewrite_questions = rewrite_query(question)
    if len(rewrite_questions) > 1:
        logger.debug('Query 改写: %s -> %s', question[:30], rewrite_questions)

    results = _retrieve_pipeline(
        question=question,
        user_context=user_context,
        parsed_filters=parsed_filters,
        top_k=top_k,
        min_score=min_score,
        rewrite_questions=rewrite_questions,
    )

    # 二次检索：只在首次为空/低置信时触发，次数受 RAG_RETRY_MAX_ATTEMPTS 限制。
    attempts = 0
    while attempts < RAG_RETRY_MAX_ATTEMPTS:
        need_retry, reason = _should_retry_retrieval(results)
        if not need_retry:
            break
        attempts += 1
        first_count = len(results)
        first_top = _retrieval_top_score(results)
        retry_results = retrieve_wider(
            question=question,
            user_context=user_context,
            parsed_filters=parsed_filters,
            top_k=RAG_RETRY_TOP_K,
            min_score=RAG_RETRY_MIN_SCORE,
            rewrite_questions=rewrite_questions,
        )
        results = _merge_retry_results(results, retry_results)
        logger.info(
            '[RAG二次检索] 第%d次 触发=%s 首次=%d(最高%.3f) 重试=%d 合并=%d',
            attempts, reason, first_count, first_top,
            len(retry_results), len(results),
        )
        if not retry_results:
            break

    if top_k and len(results) > top_k:
        results = results[:top_k]
    return results
