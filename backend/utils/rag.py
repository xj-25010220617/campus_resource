"""
RAG 检索管道
- 问题向量化（含 LRU 缓存）→ Milvus 搜索 → 关键词匹配 → 重排序 → Top-K 结果
- 上下文组装：将检索结果拼接为 LLM Prompt
"""
import contextvars
import logging
import threading
import time
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from .llm import llm_client
from .milvus import milvus_client
from .timefmt import format_business_time, month_bounds, to_iso, year_bounds
from .tracing import trace_log

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


# ------------------------------------------------------------------
# 动态学校知识库 —— 从数据库实时拼接，fallback 到 NEUSOFT_KNOWLEDGE
# ------------------------------------------------------------------
def build_school_knowledge_from_db() -> str:
    """优先从数据库读取 SchoolProfile / College / Discipline /
    Scholarship / Tuition / AdmissionRule / AcademicCalendar 拼出 Markdown。
    任何环节异常（DB 未就绪 / 表不存在 / 种子数据未灌）均 fallback 到硬编码版本。
    """
    try:
        from apps.accounts.models import (
            SchoolProfile, College, Scholarship, Tuition,
            AdmissionRule, AcademicCalendar,
        )
        from apps.classify.models import Discipline
    except Exception:
        return NEUSOFT_KNOWLEDGE

    # —— 1. 学校概况 ——
    try:
        sp = SchoolProfile.objects.first()
    except Exception:
        sp = None
    if not sp:
        return NEUSOFT_KNOWLEDGE

    lines = []
    lines.append('## 学校概况')
    lines.append(f'- 学校全称：{sp.name_full}（{sp.name_en}）' if sp.name_en else f'- 学校全称：{sp.name_full}')
    if sp.website:
        lines.append(f'- 官网：{sp.website}')
    if sp.founded_at:
        lines.append(f'- 成立时间：{sp.founded_at}年' +
                     (f'（前身为成都东软信息技术职业学院）' if sp.founded_at == 2003 else ''))
        if sp.upgraded_to_bachelor:
            lines.append(f'- 升格本科：{sp.upgraded_to_bachelor}年')
    if sp.nature:
        lines.append(f'- 办学性质：{sp.nature}')
    if sp.level:
        lines.append(f'- 办学层次：{sp.level}')
    if sp.address:
        lines.append(f'- 所在地：{sp.address}')
    if sp.area_m2:
        lines.append(f'- 占地面积：约 {sp.area_m2} 万平方米' +
                     (f'，建筑面积约 {sp.building_area_m2} 万平方米' if sp.building_area_m2 else ''))
    if sp.student_count:
        lines.append(f'- 在校生：约 {sp.student_count} 人')
    if sp.teacher_count:
        lines.append(f'- 教职工：{sp.teacher_count} 人，其中专任教师 {sp.fulltime_teacher_count} 人' +
                     (f'，外聘教师 {sp.external_teacher_count} 人' if sp.external_teacher_count else '') +
                     (f'；专任教师中"双师型"教师 {sp.double_qualified_count} 人' if sp.double_qualified_count else '') +
                     (f'，高级职称 {sp.senior_title_count} 人' if sp.senior_title_count else '') +
                     (f'，硕/博士 {sp.phd_master_count} 人（占比 {sp.phd_master_pct}%）' if sp.phd_master_count else ''))
    if sp.motto:
        lines.append(f'- 校训：{sp.motto}')
    if sp.president:
        lines.append(f'- 校长：{sp.president}')
    if sp.party_secretary:
        lines.append(f'- 党委书记：{sp.party_secretary}')
    # 完整领导班子
    if sp.leaders_text:
        lines.append('- 校领导班子：')
        for _line in sp.leaders_text.strip().splitlines():
            _line = _line.strip()
            if _line:
                lines.append(f'  - {_line}')
    if sp.school_code:
        lines.append(f'- 学校标识码：{sp.school_code}' +
                     (f'，四川省招生代码：{sp.admission_code_sichuan}' if sp.admission_code_sichuan else ''))
    if sp.disciplines_text:
        lines.append(f'- 学科门类：{sp.disciplines_text}')
    if sp.major_count:
        lines.append(f'- 本科专业：{sp.major_count} 个' +
                     (f'，另有 {sp.vocational_major_count} 个高职专科专业' if sp.vocational_major_count else ''))
    if sp.graduate_total:
        lines.append(f'- 累计培养毕业生：{sp.graduate_total} 余人' +
                     (f'，本科毕业生平均就业率 {sp.employment_rate}%' if sp.employment_rate else ''))
    if sp.partner_text:
        lines.append(f'- 合作企业：{sp.partner_text}')
    if sp.training_model:
        lines.append(f'- TOPCARES 一体化人才培养模式：{sp.training_model.strip()}')
    if sp.semester_system:
        lines.append(f'- 学期制度：{sp.semester_system.strip()}')

    # —— 2. 院系设置 ——
    colleges = list(College.objects.prefetch_related('majors').all().order_by('sort_order'))
    if colleges:
        lines.append('')
        lines.append(f'## 院系设置（{len(colleges)} 个二级学院）')
        for i, c in enumerate(colleges, 1):
            lines.append('')
            lines.append(f'### {i}. {c.name}（{c.code}）')
            if c.dean:
                lines.append(f'- 院长：{c.dean}')
            if c.secretary:
                lines.append(f'- 党委书记：{c.secretary}')
            if c.intro:
                lines.append(c.intro.strip())
            majors = list(c.majors.all().order_by('sort_order'))
            if majors:
                # 只列专业名称 + 一流专业标记，避免太长
                major_items = []
                for m in majors:
                    tag = ''
                    if m.is_first_class:
                        tag = '（国家级一流本科专业建设点）' if m.specialty_type == '国家级' else '（省级一流专业）'
                    elif m.admission_batch == '一本':
                        tag = '（一本招生）'
                    major_items.append(f'{m.name}{tag}')
                lines.append('- 专业：' + '、'.join(major_items))
            elif c.specialties_text:
                lines.append(f'- 专业：{c.specialties_text}')
            if c.features:
                lines.append(f'- 特色：{c.features.strip()}')

    # —— 3. 奖助学金 ——
    scholarships = list(Scholarship.objects.all().order_by('sort_order'))
    if scholarships:
        lines.append('')
        lines.append('## 奖助学金')
        # 按 level 分组
        groups = {}
        for s in scholarships:
            groups.setdefault(s.level, []).append(s)
        for level_label in ['enterprise', 'national', 'provincial', 'university']:
            group = groups.get(level_label, [])
            if not group:
                continue
            lines.append(f'- {dict(Scholarship.LEVEL_CHOICES).get(level_label, level_label)}：')
            for s in group:
                amt = f'（{s.amount}）' if s.amount else ''
                lines.append(f'  - {s.name}{amt}')

    # —— 4. 学费 ——
    tuitions = list(Tuition.objects.all())
    if tuitions:
        lines.append('')
        lines.append('## 学费标准')
        for t in tuitions:
            lines.append(f'- {t.category}：{t.amount_text}')

    # —— 5. 录取规则（最新） ——
    try:
        admission = AdmissionRule.objects.order_by('-year').first()
    except Exception:
        admission = None
    if admission and admission.content:
        lines.append('')
        lines.append(f'## {admission.year} 年录取规则')
        lines.append(admission.content.strip())

    # —— 6. 学期制度 ——
    calendars = list(AcademicCalendar.objects.all().order_by('sort_order'))
    if calendars:
        lines.append('')
        lines.append('## 学期与假期安排')
        for cal in calendars:
            note = f' — {cal.note}' if cal.note else ''
            lines.append(f'- {cal.term_name}{note}')

    # —— 7. 联系方式（如果概况里没有，硬编码补充）——
    lines.append('')
    lines.append('## 联系方式')
    if sp.address:
        lines.append(f'- 地址：{sp.address}' +
                     (f'（邮编：{sp.postal_code}）' if sp.postal_code else ''))
    if sp.admission_phone:
        lines.append(f'- 招生电话：{sp.admission_phone}')
    if sp.admission_email:
        lines.append(f'- 招生邮箱：{sp.admission_email}')
    if sp.admission_website:
        lines.append(f'- 招生信息网：{sp.admission_website}')

    return '\n'.join(lines)


SYSTEM_PROMPT = """你是成都东软学院的AI智能助手，负责回答学院教学、科研、管理相关问题以及常识性问题。

## 学校内建知识库（可直接使用）
{knowlege}

## 外部检索资料（补充信息）
---
{references}
---

## 实时上下文（每次请求可能不同）
{context}

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
   不得把文件内容里的日期、发布日期/内容截止日期或当前时间当作上传时间。
   若参考资料没有索引完成时间，必须说明“暂无索引完成时间/尚未完成索引”，禁止编造或拿上传时间代替。
9. 同一事项存在多份资料且「发布日期」不同时，必须以发布日期最新的一份为准；
   若因用户明确要求而引用较早版本，必须说明“该版本发布于 YYYY-MM-DD，可能已被 YYYY-MM-DD 版本更新”，
   并优先给出最新版本的结论。
   参考资料中标注「已被《X》替代」的文档属于历史版本，只能用于说明沿革，不得作为现行规定作答。"""


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
# 时效加权（多份政策冲突时优先采用最新版本）
# ------------------------------------------------------------------
# 只对时效敏感的类目做衰减：教学课件/试题库/实验指导等资料没有"过期"概念，
# 统一衰减会把多年积累的优质资料压到后面，反而降低召回质量。
# 衰减公式：MIN_FACTOR + (1-MIN_FACTOR) * 0.5 ** (days / HALF_LIFE)
#   今天发布 → 1.0；一年前 → 0.75；两年后趋近于 0.5（只降权，不淘汰）。
RAG_RECENCY_ENABLED = True
RAG_RECENCY_HALF_LIFE_DAYS = 365
RAG_RECENCY_MIN_FACTOR = 0.5
RAG_RECENCY_CATEGORY_NAMES = ('规章制度', '培养方案', '官网公告', '新闻动态')

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
                'superseded_by__title',
            )
        }
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get('doc_title', '未知文档')
        page = chunk.get('page', 0)
        content = chunk.get('parent_content') or chunk.get('content', '')
        heading_path = chunk.get('heading_path') or ''
        if heading_path:
            content = f'（{heading_path}）\n{content}'
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
            # 明确标注为「发布日期」：LLM 据此判断新旧版本，不得当作上传时间
            ref_block += f'，发布日期：{freshness.isoformat()}'
        superseded_title = doc_time.get('superseded_by__title')
        if superseded_title:
            ref_block += f'，已被《{superseded_title}》替代（历史版本）'
        ref_block += f'）\n{content}'
        total_chars += len(ref_block)
        if total_chars > max_chars:
            break
        ref_lines.append(ref_block)

    refs_text = '\n\n'.join(ref_lines) if ref_lines else '暂无相关参考资料。'
    from utils.context import build_system_context
    system_content = SYSTEM_PROMPT.format(
        knowlege=build_school_knowledge_from_db(),
        references=refs_text,
        context=build_system_context(),
    )

    # 证据不足标记（由 retrieve 写入）：明确要求模型在资料找不到答案时
    # 回答"知识库暂无"，而不是用常识补全。仅提示，不改变正常作答路径。
    if any(c.get('evidence_insufficient') for c in chunks):
        system_content += (
            '\n\n## 证据充分性提示\n'
            '本轮检索到的资料可能不足以完整回答问题。请仅依据参考资料作答；'
            '若资料中找不到答案，必须明确回答「知识库中暂无相关说明」，'
            '并建议用户补充上传或联系管理员，禁止推测、编造或使用你的先验知识补全。'
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
            'parent_index': chunk.parent_index if chunk else -1,
            'heading_path': chunk.heading_path if chunk else '',
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
        # 匹配强度分：所有文档原本都是常数 0.55，通道内并列时排序由数据库
        # 默认顺序决定（等价随机）。RRF 只看名次不看分数，名次随机会直接
        # 传导成噪声；这里按"查询词覆盖率"给文档排出真实先后（标题权重更高）。
        title_lower = (doc['title'] or '').lower()
        meta_lower = ' '.join([
            title_lower, (doc['description'] or '').lower(),
            (doc['file_name'] or '').lower(),
        ])
        n_terms = max(len(keywords), 1)
        title_cov = sum(1 for kw in keywords if kw.lower() in title_lower) / n_terms
        meta_cov = sum(1 for kw in keywords if kw.lower() in meta_lower) / n_terms
        results.append({
            'doc_id': doc['id'],
            'doc_title': doc['title'],
            'content': f"{doc['title']} {excerpt}".strip(),
            # score 保持常数 0.55：加权融合已验证该口径下排序最稳（见评测基线），
            # match_strength 只供 RRF 在通道内排真实先后（RRF 不看分数只看名次）。
            'score': 0.55,
            'match_strength': round(2.0 * title_cov + meta_cov, 4),
            'page': 0,
            'source_type': doc['source_type'],
            'source_url': doc['source_url'],
            'file_name': doc['file_name'],
            'oss_key': doc['oss_key'],
        })
    results.sort(key=lambda r: r['score'], reverse=True)
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


# ------------------------------------------------------------------
# 标签检索通道（第 5 路召回）
# ------------------------------------------------------------------
# 标签是人工打标、全平台共享的词表，不带检索权重、不做同义扩展，
# 因此匹配方式必须是"词表倒查问题文本"（tag.name in question），
# 而不是把问题切碎去匹配标签——后者受 _extract_query_terms 的
# 8 词上限和停用词表截断，长标签会被切碎漏配。
_TAG_CACHE_TTL = 60.0  # 标签词表内存缓存有效期（秒），打标后最迟 1 分钟生效
_tag_cache: dict = {'terms': None, 'expires_at': 0.0}
_tag_cache_lock = threading.Lock()


def _get_tag_terms() -> List[Tuple[int, str]]:
    """全量标签词表 [(tag_id, name)]，按名称长度降序（长标签优先）。"""
    now = time.time()
    if _tag_cache['terms'] is not None and now < _tag_cache['expires_at']:
        return _tag_cache['terms']
    with _tag_cache_lock:
        now = time.time()
        if _tag_cache['terms'] is not None and now < _tag_cache['expires_at']:
            return _tag_cache['terms']
        from apps.knowledge.models import Tag
        terms = sorted(
            Tag.objects.filter(is_deleted=False).values_list('id', 'name'),
            key=lambda t: len(t[1]), reverse=True,
        )
        _tag_cache['terms'] = terms
        _tag_cache['expires_at'] = now + _TAG_CACHE_TTL
        return terms


def invalidate_tag_cache():
    """标签增删后调用，立即失效词表缓存（不等 TTL）。"""
    _tag_cache['terms'] = None
    _tag_cache['expires_at'] = 0.0


def _match_tags(question: str) -> List[dict]:
    """倒查标签词表：返回问题文本中出现的标签。

    纯内存子串匹配，零 LLM 调用（问答链路首字延迟预算 30s，
    不能再加一次模型调用）。去掉被更长命中标签名包含的短标签
    （如同时命中"奖学金"与"奖学金评定"时只保留后者），降低歧义。

    Returns:
        [{'tag_id': int, 'name': str}, ...]，按名称长度降序。
    """
    q = (question or '').lower()
    if not q:
        return []
    matched = [
        {'tag_id': tid, 'name': name}
        for tid, name in _get_tag_terms()
        if len(name) >= 2 and name.lower() in q
    ]
    return [
        m for m in matched
        if not any(o['name'] != m['name'] and m['name'] in o['name'] for o in matched)
    ]


def _tag_table_search(
    tags: List[dict],
    user_context: Optional[dict] = None,
    top_k: int = 5,
    doc_ids: Optional[List[int]] = None,
) -> List[dict]:
    """标签表检索通道：按人工标签反查文档。

    与 _keyword_table_search 同构：先查 DocTag 得到 doc_id 集合，
    再统一经过 authorized_knowledge_docs() 权限过滤和 status='indexed'
    限制，标签通道不得成为越权读取通道。
    """
    if not tags:
        return []

    from apps.knowledge.models import DocTag

    tag_ids = [t['tag_id'] for t in tags]
    doc_id_qs = DocTag.objects.filter(tag_id__in=tag_ids).values('doc_id')
    docs = authorized_knowledge_docs(user_context).filter(
        id__in=doc_id_qs, status='indexed',
    )
    if doc_ids is not None:
        docs = docs.filter(id__in=[d for d in doc_ids if d > 0])
    docs = docs.order_by('-created_at')[:top_k]

    return _build_doc_results(docs, [t['name'] for t in tags], score=0.6)


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

    # 单字词在 ngram(2) 索引里检索不到，先滤掉，避免"能查却查不到"的假阴性
    keywords = [kw for kw in keywords if kw and len(kw) >= 2]
    if not keywords:
        return []

    from django.db.models import Q
    from apps.knowledge.models import DocChunk

    allowed_docs = authorized_knowledge_docs(user_context).filter(
        status='indexed',
    )
    if doc_ids is not None:
        allowed_docs = allowed_docs.filter(id__in=[d for d in doc_ids if d > 0])

    base_qs = (
        DocChunk.objects
        .filter(doc__in=allowed_docs)
        .select_related('doc')
        .order_by('doc_id', 'chunk_index')
    )

    chunks = []
    used_fulltext = False
    ft_query = ' '.join(dict.fromkeys(keywords))
    if ft_query and getattr(settings, 'RAG_FULLTEXT_ENABLED', True):
        try:
            chunks = list(
                base_qs.extra(
                    where=['MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE)'],
                    params=[ft_query],
                )[:top_k * 3]
            )
            used_fulltext = True
        except Exception as e:
            # 索引缺失/后端不支持（如 SQLite）时静默回退，功能优先于性能
            logger.warning('FULLTEXT 检索不可用，回退 icontains: %s', e)

    # 兜底：FULLTEXT 未生效，或 ngram 分词没命中（长词被切成 2-gram 后仍可能有
    # 边界差异）时，用原 icontains 再查一次，保证召回不因提速而退化
    if not chunks:
        if used_fulltext:
            trace_log('rag_chunk_ft_empty', 'keywords=%s', ft_query)
        chunk_query = Q()
        for kw in keywords:
            chunk_query |= Q(content__icontains=kw)
        chunks = list(base_qs.filter(chunk_query)[:top_k * 3])

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
            'parent_index': hit.get('parent_index', -1),
            'heading_path': hit.get('heading_path', ''),
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
                'parent_index': hit.get('parent_index', -1),
                'heading_path': hit.get('heading_path', ''),
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
# 融合算法切换与阈值解析
# ------------------------------------------------------------------
# 加权融合与 RRF 的分数完全不重量纲（加权 ~0.x，RRF ~0.0x），
# 所有阈值必须按当前算法解析，禁止把 RAG_SCORE_THRESHOLD 直接套到 RRF 上。
def fusion_mode() -> str:
    """当前启用的融合算法：weighted | rrf。"""
    mode = str(getattr(settings, 'RETRIEVAL_FUSION', 'weighted') or 'weighted').lower()
    return mode if mode in ('weighted', 'rrf') else 'weighted'


def active_score_threshold() -> float:
    if fusion_mode() == 'rrf':
        return float(getattr(settings, 'RRF_SCORE_THRESHOLD', 0.008))
    return RAG_SCORE_THRESHOLD


def active_retry_trigger() -> float:
    if fusion_mode() == 'rrf':
        return float(getattr(settings, 'RRF_RETRY_TRIGGER_SCORE', 0.015))
    return RAG_RETRY_TRIGGER_SCORE


def active_reference_threshold() -> float:
    """引用展示阈值：加权下沿用 min(0.65, 0.35) 的既有口径，RRF 下独立标定。"""
    if fusion_mode() == 'rrf':
        return float(getattr(settings, 'RRF_REFERENCE_THRESHOLD', 0.02))
    return min(RAG_REFERENCE_THRESHOLD, 0.35)


def active_admin_min_score() -> float:
    """管理员低置信拒答阈值（按融合算法解析，RRF 下 0.5 会拒掉全部结果）。"""
    if fusion_mode() == 'rrf':
        return float(getattr(settings, 'RRF_ADMIN_MIN_SCORE', 0.03))
    return ADMIN_MIN_SCORE


def active_web_search_min_score() -> float:
    """联网兜底触发阈值（按融合算法解析）。"""
    if fusion_mode() == 'rrf':
        return float(getattr(settings, 'RRF_WEB_SEARCH_MIN_SCORE', 0.03))
    return WEB_SEARCH_MIN_SCORE


# ------------------------------------------------------------------
# 向量通道质量门
# ------------------------------------------------------------------
# 向量最高分过低（Embedding 失效/维度错配）或分数几乎没有差异（无区分度）时，
# 说明向量通道这次没有提供有效排序信息，降权让关键词通道主导，避免噪声块
# 挤掉真正命中的文档。向量为空时不惩罚关键词侧（见 _merge_and_rerank）。
# 阈值可由 settings 覆盖：不同 Embedding 后端的余弦分布差异很大，
# 换模型/换集合后必须用评测重新标定（scripts/eval_retrieval.py）。
_VECTOR_GATE_LOW_TOP = 0.25
_VECTOR_GATE_MIN_SPREAD = 0.02
_VECTOR_WEIGHT_NORMAL = 0.7
_VECTOR_WEIGHT_GATED = 0.2


def _vector_channel_weight(vector_hits: List[dict]) -> Tuple[float, str]:
    """返回 (向量权重, 触发原因)。未触发质量门时权重为 0.7。"""
    if not vector_hits:
        return _VECTOR_WEIGHT_GATED, 'no_vector'
    if not bool(getattr(settings, 'RAG_VECTOR_GATE_ENABLED', True)):
        return _VECTOR_WEIGHT_NORMAL, ''
    low_top = float(getattr(settings, 'RAG_VECTOR_GATE_LOW_TOP', _VECTOR_GATE_LOW_TOP))
    min_spread = float(
        getattr(settings, 'RAG_VECTOR_GATE_MIN_SPREAD', _VECTOR_GATE_MIN_SPREAD),
    )
    scores = [float(h.get('score', 0) or 0) for h in vector_hits]
    top = max(scores)
    spread = top - min(scores)
    if top < low_top:
        return _VECTOR_WEIGHT_GATED, f'low_top={top:.3f}'
    if len(scores) > 1 and spread < min_spread:
        return _VECTOR_WEIGHT_GATED, f'low_spread={spread:.4f}'
    return _VECTOR_WEIGHT_NORMAL, ''


# ------------------------------------------------------------------
# RRF 倒数排名融合
# ------------------------------------------------------------------
def _best_by_doc(hits: List[dict]) -> List[dict]:
    """关键词类通道按文档聚合：同文档只保留最高分一条，再按分数降序。

    关键词通道的命中是"文档级证据"，同文档多块逐条计票会让长文档靠
    分块数量堆高排名（见 _merge_and_rerank 同名逻辑）。

    通道内名次：优先 match_strength（查询词覆盖率，由 _keyword_search 计算），
    其次 score；都没有时退化为原顺序。RRF 只看名次，通道内并列会让排序
    变成数据库默认序（等价随机），必须用覆盖率把并列拆开。
    """
    best: Dict[int, dict] = {}
    for hit in hits or []:
        did = hit.get('doc_id')
        if did is None:
            continue
        key = (hit.get('match_strength', 0), hit.get('score', 0))
        if did not in best:
            best[did] = hit
            continue
        cur = (best[did].get('match_strength', 0), best[did].get('score', 0))
        if key > cur:
            best[did] = hit
    return sorted(
        best.values(),
        key=lambda h: (h.get('match_strength', 0), h.get('score', 0)),
        reverse=True,
    )


def _rrf_merge_and_rerank(
    vector_hits: List[dict],
    keyword_channels: List[List[dict]],
    k: int = 60,
    vector_weight: float = 1.0,
    keywords_boost: Optional[dict] = None,
) -> List[dict]:
    """RRF 融合：score(d) = Σ_channel w_c / (k + rank_c(d) + 1)。

    所有通道都按 **文档级** 计票：一个文档在某个通道内只贡献一票，
    名次取该文档在该通道内最靠前的位置。若按块级计票，向量通道一次
    返回 15 个块会把"只有一块命中的长文档"和"被 15 个块覆盖的文档"
    拉开数量级差距，精确命中的单块文档反而被挤下去（实测 Recall@5
    比加权融合低 10pt）。块级差异只用于同文档内的展示顺序。

    Returns:
        与 _merge_and_rerank 同构的结果列表（同文档多块保留，供拼上下文）。
    """
    keywords_boost = keywords_boost or {}

    doc_scores: Dict[int, float] = {}
    doc_best: Dict[int, dict] = {}
    vector_order: Dict[int, int] = {}   # 文档在向量通道中的最好名次
    vector_chunks: List[dict] = []

    # 1) 向量通道（文档级计票，块级保留）
    for rank, hit in enumerate(vector_hits or []):
        did = hit.get('doc_id')
        if did is None:
            continue
        vector_chunks.append(hit)
        if did not in vector_order:
            vector_order[did] = rank
            doc_scores[did] = doc_scores.get(did, 0.0) + vector_weight / (k + rank + 1)
            doc_best[did] = hit

    # 2) 关键词类通道（文档级计票）
    for hits in keyword_channels:
        for rank, hit in enumerate(_best_by_doc(hits)):
            did = hit.get('doc_id')
            doc_scores[did] = doc_scores.get(did, 0.0) + 1.0 / (k + rank + 1)
            doc_best.setdefault(did, hit)

    # 3) 向量块 → 结果条目：同文档共享文档分，用极小 epsilon 保持块内原顺序
    merged: Dict[str, dict] = {}
    total_chunks = max(len(vector_chunks), 1)
    for idx, hit in enumerate(vector_chunks):
        did = hit.get('doc_id')
        key = hit.get('vector_id') or f"v_{did}_{hit.get('chunk_index')}"
        merged[key] = {
            'doc_id': did,
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id'),
            'content': hit.get('chunk_text', ''),
            'page': hit.get('page_number', 0),
            'doc_title': hit.get('doc_title', ''),
            'source_type': hit.get('source_type', ''),
            'source_url': hit.get('source_url', ''),
            'file_name': hit.get('file_name', ''),
            'oss_key': hit.get('oss_key', ''),
            'score': doc_scores.get(did, 0.0) + (total_chunks - idx) * 1e-6,
        }

    # 4) 只有关键词侧命中的文档：单独成条
    for did, score in doc_scores.items():
        if did in vector_order:
            continue
        hit = doc_best[did]
        merged[f"kw_{did}"] = {
            'doc_id': did,
            'doc_title': hit.get('doc_title', ''),
            'content': hit.get('content', ''),
            'page': hit.get('page', 0),
            'vector_id': '',
            'chunk_index': hit.get('chunk_index'),
            'score': score,
            'source_type': hit.get('source_type', ''),
            'source_url': hit.get('source_url', ''),
            'file_name': hit.get('file_name', ''),
            'oss_key': hit.get('oss_key', ''),
        }

    # 5) 关键词权重加成：换算成"一票"量级（weight=10 ≈ 一次榜首命中）
    if keywords_boost:
        unit = 1.0 / (k + 1)
        for hit in merged.values():
            content = (hit.get('content') or '').lower()
            for word, weight in keywords_boost.items():
                if word and word.lower() in content:
                    hit['score'] += unit * (weight / 10.0)

    return sorted(merged.values(), key=lambda x: x['score'], reverse=True)


# ------------------------------------------------------------------
# 文档授权范围（所有检索路径共用）
# ------------------------------------------------------------------
def authorized_knowledge_docs(
    user_context: Optional[dict] = None,
    include_superseded: bool = False,
):
    """返回当前用户可检索的 KnowledgeDoc QuerySet。

    授权逻辑必须在召回和引用签名前保持一致。可见性不再影响检索：public 与
    private 文档都参与智能问答召回，二者只在下载环节区分（见
    KnowledgeDocDownloadView，private 仅上传者本人与超管可下载）。

    例外：超管专属空间的 private 文档仍仅该超管本人可检索，避免个人文件
    进入公共问答。其他角色按学校/本部门/本人上传范围检索。

    include_superseded=True 时不再排除已被新版本替代的旧版文档：仅用于
    「用户显式 @ 了旧版文档」的场景，让用户能针对历史版本提问；默认 False，
    保证常规检索与标签召回不会混入过期版本。
    """
    from django.db.models import Q
    from apps.knowledge.acl import school_level_q
    from apps.knowledge.models import KnowledgeDoc

    user_context = user_context or {}
    role = user_context.get('role', 'user')
    user_id = user_context.get('user_id')
    college_id = user_context.get('college_id')
    department_id = user_context.get('department_id')

    qs = KnowledgeDoc.objects.filter(review_status='approved')
    if not include_superseded:
        # 已被新版本替代的旧版默认不参与问答检索
        qs = qs.filter(superseded_by__isnull=True)

    # 超管专属空间的私密文档：仅该超管本人可检索（与浏览链路 _admin_scope_filter 对齐）
    super_private = Q(visibility='private', uploader__role='super_admin')
    if role == 'super_admin':
        return qs.exclude(super_private & ~Q(uploader_id=user_id))

    # 可见性不参与检索过滤：private 文档同样可被问答搜到，仅限制下载
    qs = qs.exclude(super_private)
    # 校级文档：无学院/部门归属，且没有残留的旧 scope 标记（与浏览链路共用判定）
    school_level = school_level_q()
    if role in ('admin', 'college_admin'):
        conditions = school_level
        if user_id:
            conditions |= Q(uploader_id=user_id)
        if college_id:
            # 未绑定学院时不拼 Q(college_id=None)，否则会命中所有部门级文档
            conditions |= Q(college_id=college_id)
        return qs.filter(conditions)
    if role == 'dept_admin':
        conditions = school_level
        if user_id:
            conditions |= Q(uploader_id=user_id)
        if department_id:
            conditions |= Q(department_id=department_id)
        return qs.filter(conditions)

    conditions = school_level
    if college_id:
        conditions |= Q(college_id=college_id)
    if department_id:
        conditions |= Q(department_id=department_id)
    if user_id:
        conditions |= Q(uploader_id=user_id)
    return qs.filter(conditions)


# ------------------------------------------------------------------
# 提问范围限定（@文档 / #标签）
# ------------------------------------------------------------------
SCOPE_MAX_DOCS = 50  # @/# 引用限定的文档数量上限，超出截断并提示用户缩小范围


def resolve_scope_doc_ids(
    doc_ids: Optional[List[int]] = None,
    tag_ids: Optional[List[int]] = None,
    user_context: Optional[dict] = None,
) -> dict:
    """把用户 @ 的文档与 # 的标签解析为可检索的 doc_id 集合。

    - @文档：取显式指定的 doc_id（允许是已被新版本替代的旧版，便于针对历史版本提问）；
    - #标签：取这些标签（多个取并集）关联的全部文档，旧版仍被排除；
    - 统一经过 authorized_knowledge_docs() 权限过滤 + status='indexed'，
      与浏览/检索链路的授权口径保持一致（无权/未索引的引用静默剔除）。

    Returns:
        {'doc_ids': [...], 'doc_titles': [...], 'tag_names': [...],
         'superseded_pairs': [{'title': 旧版标题, 'superseded_by_title': 新版标题}]}
    """
    from apps.knowledge.models import DocTag, Tag

    user_context = user_context or {'role': 'user'}
    requested_doc_ids = sorted({
        int(i) for i in (doc_ids or []) if isinstance(i, int) and i > 0
    })
    requested_tag_ids = sorted({
        int(i) for i in (tag_ids or []) if isinstance(i, int) and i > 0
    })

    tag_names: List[str] = []
    if requested_tag_ids:
        tag_names = list(
            Tag.objects.filter(id__in=requested_tag_ids, is_deleted=False)
            .values_list('name', flat=True)
        )

    tag_doc_ids: List[int] = []
    if requested_tag_ids:
        tag_doc_ids = list(
            DocTag.objects.filter(
                tag_id__in=requested_tag_ids,
                tag__is_deleted=False,
            ).values_list('doc_id', flat=True)
        )

    field_names = ('id', 'title', 'superseded_by_id', 'superseded_by__title')
    docs_by_id = {}

    # @显式指定的文档：允许旧版（用户可能就是要查历史版本）
    if requested_doc_ids:
        for row in (
            authorized_knowledge_docs(user_context, include_superseded=True)
            .filter(id__in=requested_doc_ids, status='indexed')
            .values(*field_names)
        ):
            docs_by_id[row['id']] = row

    # #标签带出的文档：仍排除已被替代的旧版，避免范围里混入过期内容
    tag_doc_id_set = set(tag_doc_ids) - set(requested_doc_ids)
    if tag_doc_id_set:
        for row in (
            authorized_knowledge_docs(user_context)
            .filter(id__in=tag_doc_id_set, status='indexed')
            .values(*field_names)
        ):
            docs_by_id[row['id']] = row

    if not docs_by_id:
        return {
            'doc_ids': [], 'doc_titles': [], 'tag_names': tag_names,
            'superseded_pairs': [], 'truncated': False,
        }

    truncated = len(docs_by_id) > SCOPE_MAX_DOCS
    docs = sorted(docs_by_id.values(), key=lambda d: d['id'])[:SCOPE_MAX_DOCS]
    if truncated:
        logger.info('@/# 引用范围过大已截断: 命中 %d 份，仅纳入前 %d 份',
                    len(docs_by_id), SCOPE_MAX_DOCS)
    superseded_pairs = [
        {
            'title': d['title'],
            'superseded_by_title': d.get('superseded_by__title') or '',
        }
        for d in docs if d.get('superseded_by_id')
    ]
    return {
        'doc_ids': [d['id'] for d in docs],
        'doc_titles': [d['title'] for d in docs],
        'tag_names': tag_names,
        'superseded_pairs': superseded_pairs,
        'truncated': truncated,
    }


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
# 标签穷举通道（"列出标签X下所有文件"类查询返回全量文档列表）
# ------------------------------------------------------------------
# 文件列举意图词：穷举/聚合词表偏向"人员名单"语义，补充文件维度说法
_TAG_LISTING_INTENT_KEYWORDS = (
    '哪些文件', '哪些文档', '哪些资料', '都有什么', '有什么文件',
    '有什么文档', '文件有哪些', '文档有哪些', '文件列表', '文档列表', '大全',
)

TAG_LISTING_MAX_DOCS = 50  # 单次列举文档上限（50 条 × ~150 字 ≈ 7.5K，低于穷举字符上限）


def is_tag_listing_question(question: str) -> bool:
    """检测文件列举意图：与穷举/聚合意图并列为标签穷举的触发条件。"""
    q = (question or '').strip()
    return any(k in q for k in _TAG_LISTING_INTENT_KEYWORDS)


def try_expand_tag_listing(
    question: str,
    user_context: Optional[dict] = None,
    max_docs: int = TAG_LISTING_MAX_DOCS,
) -> Tuple[Optional[List[dict]], Optional[str]]:
    """标签穷举：问题命中恰好一个标签且含列举意图时，返回该标签下全部文档列表。

    与 try_expand_enumeration（单文档全部分块）互补：本通道以"每文档一条"
    的形式给出标签范围内的完整文件清单，实现"用标签提问→快速定位文件"。

    触发条件（全部满足）：
    - 穷举 / 聚合 / 文件列举意图之一
    - _match_tags 恰好命中 1 个标签（0 个无目标，≥2 个歧义不猜）
    - 该标签下存在授权且已索引的文档

    必须复用 authorized_knowledge_docs() 权限过滤 + status='indexed'，
    标签穷举不得成为越权读取通道。

    Returns:
        (文档列表, 标签名)；未触发时 (None, None)。
    """
    if not (
        is_enumeration_question(question)
        or is_aggregation_question(question)
        or is_tag_listing_question(question)
    ):
        return None, None

    tags = _match_tags(question)
    if len(tags) != 1:
        return None, None
    tag = tags[0]

    from apps.knowledge.models import DocTag
    doc_id_qs = DocTag.objects.filter(tag_id=tag['tag_id']).values('doc_id')
    docs = authorized_knowledge_docs(user_context).filter(
        id__in=doc_id_qs, status='indexed',
    ).order_by('-created_at')[:max_docs]

    results = []
    for doc in docs:
        desc = (doc.description or '').strip()
        summary = f"《{doc.title}》（标签：{tag['name']}"
        if doc.file_name:
            summary += f"，文件名：{doc.file_name}"
        summary += '）'
        if desc:
            summary += f' 简介：{desc[:120]}'
        results.append({
            'doc_id': doc.id,
            'doc_title': doc.title,
            'content': summary,
            'score': 0.7,  # 高于 RAG_REFERENCE_THRESHOLD，保证进入引用列表
            'page': 0,
            'chunk_index': None,
            'vector_id': '',
            'source_type': doc.source_type,
            'source_url': doc.source_url,
            'file_name': doc.file_name,
            'oss_key': doc.oss_key,
        })
    if not results:
        return None, None
    return results, tag['name']


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
def _recency_factor(publish_date, today=None) -> float:
    """按发布日期计算时效因子，用于「多份政策冲突取最新」。

    发布日期为空时返回 1.0（不做任何惩罚），避免历史数据因缺日期被系统性压分。
    """
    if not publish_date:
        return 1.0
    try:
        days = max((today or timezone.localdate()) - publish_date, timedelta()).days
    except TypeError:
        return 1.0
    return RAG_RECENCY_MIN_FACTOR + (1 - RAG_RECENCY_MIN_FACTOR) * (
        0.5 ** (days / RAG_RECENCY_HALF_LIFE_DAYS)
    )


# ------------------------------------------------------------------
# 文件网 1 跳扩展（命中文档的关联文档作为低分补充候选）
# ------------------------------------------------------------------
# 仅沿「前置知识/补充材料/引用来源」扩展；replaces（新版替代）由版本机制处理；
# same_course 语义太宽，容易把整门课的资料都拉进来，不扩展。
RELATION_EXPAND_TYPES = ('prerequisite', 'supplement', 'citation')
RELATION_EXPAND_MAX = 5            # 一次检索最多扩展出的关联文档数
RELATION_EXPAND_SCORE_FACTOR = 0.6  # 关联文档分数 = 源文档分数 × 系数


def _expand_by_doc_relations(results: List[dict], user_context: dict) -> List[dict]:
    """沿命中文档的出边做 1 跳扩展，把关联文档追加为低分候选。

    仅在常规检索（非 @/# 限定范围）时启用：限定范围时用户明确要求
    「仅依据指定文档作答」，扩展会引入范围外文档，违背该意图。

    目标文档同样过 authorized_knowledge_docs() 权限过滤与 status='indexed'；
    已在结果中的文档不重复；visited 防止环。
    """
    from apps.knowledge.models import DocRelation, KnowledgeDoc

    src_by_id = {r['doc_id']: r for r in results if r.get('doc_id')}
    if not src_by_id:
        return results

    edges = list(
        DocRelation.objects.filter(
            source_doc_id__in=src_by_id.keys(),
            relation_type__in=RELATION_EXPAND_TYPES,
            is_deleted=False,
        ).values('source_doc_id', 'target_doc_id', 'relation_type')
    )
    if not edges:
        return results

    # 候选目标：排除已命中的文档（visited），经权限与索引状态过滤
    target_ids = {e['target_doc_id'] for e in edges} - set(src_by_id.keys())
    if not target_ids:
        return results
    authorized = {
        d['id']: d for d in authorized_knowledge_docs(user_context)
        .filter(id__in=target_ids, status='indexed')
        .values(
            'id', 'title', 'description', 'source_type', 'source_url',
            'file_name', 'oss_key', 'created_at', 'indexed_at', 'freshness_date',
        )
    }

    expanded: List[dict] = []
    visited = set(src_by_id.keys())
    for edge in edges:
        if len(expanded) >= RELATION_EXPAND_MAX:
            break
        tid = edge['target_doc_id']
        if tid in visited:
            continue
        meta = authorized.get(tid)
        if not meta:
            continue  # 无权限/未索引/旧版：静默跳过
        visited.add(tid)
        src = src_by_id[edge['source_doc_id']]
        expanded.append({
            'doc_id': tid,
            'doc_title': meta['title'],
            'content': f"{meta['title']}\n{(meta.get('description') or '')[:500]}",
            'score': round(src.get('score', 0) * RELATION_EXPAND_SCORE_FACTOR, 4),
            'page': 0,
            'chunk_index': None,
            'vector_id': '',
            'source_type': meta.get('source_type') or '',
            'source_url': meta.get('source_url') or '',
            'file_name': meta.get('file_name') or '',
            'oss_key': meta.get('oss_key') or '',
            'created_at': to_iso(meta.get('created_at')),
            'created_at_display': format_business_time(meta.get('created_at')),
            'indexed_at': to_iso(meta.get('indexed_at')),
            'indexed_at_display': format_business_time(meta.get('indexed_at')),
            'freshness_date': (
                meta['freshness_date'].isoformat() if meta.get('freshness_date') else None
            ),
            # 标记扩展来源，供前端/日志区分「直接命中」与「关联推荐」
            'expanded_from_doc_id': src['doc_id'],
            'expanded_relation_type': edge['relation_type'],
        })

    if expanded:
        logger.info(
            '文件网扩展: 源文档=%d 条，追加关联文档 %d 条', len(src_by_id), len(expanded),
        )
        results = sorted(results + expanded, key=lambda r: r['score'], reverse=True)
    return results


def _enrich_with_doc_meta(hits: List[dict], min_score: float) -> List[dict]:
    """
    关键词命中返回 content 字段（标题 + 描述拼接），用于补充检索结果。

    同时对「时效敏感类目」按发布日期做衰减加权：同一主题的新旧两版政策，
    新版分数更高、排到前面；旧版仍会保留（分数不低于 MIN_FACTOR 倍），
    以便回答「去年的规定是什么」这类回溯问题。
    """
    from apps.knowledge.models import KnowledgeDoc

    doc_ids = list({int(h['doc_id']) for h in hits if h.get('doc_id')})
    doc_title_map = {
        row['id']: row for row in KnowledgeDoc.objects.filter(
            id__in=doc_ids, status='indexed',
        ).values(
            'id', 'title', 'source_type', 'source_url', 'file_name', 'oss_key',
            'created_at', 'indexed_at', 'freshness_date', 'category__name',
        )
    }

    today = timezone.localdate()
    results = []
    for hit in hits:
        if not hit.get('content'):
            continue
        did = int(hit['doc_id'])
        meta = doc_title_map.get(did, {})

        score = hit['score']
        category_name = meta.get('category__name') or ''
        if RAG_RECENCY_ENABLED and category_name in RAG_RECENCY_CATEGORY_NAMES:
            score *= _recency_factor(meta.get('freshness_date'), today)
        if score < min_score:
            continue

        results.append({
            'doc_id': did,
            'doc_title': meta.get('title') or hit.get('doc_title', '未知文档'),
            'content': hit['content'],
            'score': round(score, 4),
            'page': hit.get('page', 0),
            'chunk_index': hit.get('chunk_index'),
            'vector_id': hit.get('vector_id', ''),
            # 父子块字段：从上游 merge 结果透传，若上游未带则取默认
            'parent_index': hit.get('parent_index', -1),
            'heading_path': hit.get('heading_path', ''),
            'parent_content': hit.get('parent_content'),  # 可能为 None / 字符串
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

    # 衰减后分数顺序可能变化，必须重排后再取 Top-5
    results.sort(key=lambda r: r['score'], reverse=True)
    return results[:5]


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
    if fusion_mode() == 'rrf':
        # RRF 分数尺度完全不同（~0.0x），重试阈值必须独立标定
        trigger = active_retry_trigger()
        single = trigger * 1.5
    else:
        trigger = RAG_RETRY_TRIGGER_SCORE
        single = RAG_RETRY_SINGLE_RESULT_SCORE
    if top_score < trigger:
        return True, f'low_top_score={top_score:.3f}'
    if (
        len(results) < RAG_RETRY_MIN_RESULTS
        and top_score < single
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
    apply_rerank: bool = True,
    doc_scope: Optional[List[int]] = None,
) -> List[dict]:
    """执行一次完整检索管道（硬过滤 → 多路召回 → 合并精排 → 权限校验）。

    retrieve() 与 retrieve_wider() 共用此管道，避免二次检索重复实现一套逻辑。
    doc_scope 非 None 时启用 @/# 范围限定：候选集合直接取用户引用的文档，
    跳过 SQL 硬过滤、时间降级重试与标题兜底搜索。
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
    if doc_scope is not None:
        # @/# 限定范围：候选即用户引用且通过权限/索引校验的文档
        candidate_ids = list(doc_scope)
        logger.debug('范围限定检索: %d 个指定文档', len(candidate_ids))
    else:
        candidate_ids = _apply_hard_filters(parsed_filters, user_context, top_k=500)

    # 1b. 时间条件清零时降级去掉时间重试：
    #     "15级/2015届"指内容的届别年份，而 time_range 过滤的是文档上传
    #     时间（created_at）。意图解析把届别映射成 time_range 会误杀全部
    #     候选（历史届别文档都是近期上传的），宁可放宽时间也不能零召回。
    if doc_scope is None and not candidate_ids and parsed_filters.get('time_range'):
        stripped = {k: v for k, v in parsed_filters.items() if k != 'time_range'}
        candidate_ids = _apply_hard_filters(stripped, user_context, top_k=500)
        if candidate_ids:
            logger.info(
                f'time_range={parsed_filters["time_range"]!r} 清零候选，'
                f'已降级去掉时间条件重试: 候选 {len(candidate_ids)} 个'
            )

    if not candidate_ids:
        if doc_scope is not None:
            # 限定范围内无候选：不做标题兜底，直接空召回
            return []
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

    # 2c. 标签表通道（第 5 路召回）：问题文本命中人工标签时软增强，
    #     命中标签的文档并入关键词侧统一聚合，不做硬过滤
    matched_tags = _match_tags(question)
    tag_hits = _tag_table_search(
        matched_tags, user_context, top_k=top_k, doc_ids=candidate_ids,
    ) if matched_tags else []

    # 3. 合并 + 关键词权重加成（融合算法由 settings.RETRIEVAL_FUSION 切换）
    keyword_channels = [keyword_hits, keyword_table_hits, chunk_hits, tag_hits]
    vector_weight, gate_reason = _vector_channel_weight(vector_hits)
    if gate_reason:
        logger.info('[RAG向量质量门] 向量通道降权 %.1f（%s）', vector_weight, gate_reason)
        trace_log('rag_vector_gate', 'weight=%.1f reason=%s', vector_weight, gate_reason)
    boost = _keywords_boost_from_filters(parsed_filters)
    if fusion_mode() == 'rrf':
        merged = _rrf_merge_and_rerank(
            vector_hits,
            keyword_channels,
            k=int(getattr(settings, 'RRF_K', 60)),
            vector_weight=vector_weight,
            keywords_boost=boost,
        )
    else:
        merged = _merge_and_rerank(
            vector_hits,
            keyword_hits + keyword_table_hits + chunk_hits + tag_hits,
            vector_weight=vector_weight,
            keywords_boost=boost,
        )

    # 3.5 Cross-Encoder 精排（候选 >=3 即触发）
    # 二次检索的重试轮传 apply_rerank=False：精排只在首轮跑一次，
    # 避免 CPU 上精排耗时随重试翻倍（曾导致 80~100s 超时）
    if apply_rerank:
        try:
            from utils.reranker import rerank_with_cross_encoder
            if len(merged) >= 3:
                merged = rerank_with_cross_encoder(question, merged)
                logger.debug('Cross-Encoder 重排序后: %d 条', len(merged))
                # 精排分数已校准到 [0,1]，改用精排后端的独立阈值过滤，
                # 不再复用加权融合/RRF 的分数量纲
                if any('rerank_raw_score' in c for c in merged):
                    min_score = max(
                        min_score, float(getattr(settings, 'RERANK_MIN_SCORE', 0.30)),
                    )
        except ImportError:
            pass  # reranker 模块不存在，静默跳过

    # 4. 加载 doc 元信息
    enriched = _enrich_with_doc_meta(merged, min_score)

    # 5. 权限校验
    results = _filter_by_permission(enriched, user_context)

    # 5.1 文件网 1 跳扩展：命中文档的关联文档作为低分补充候选
    # （@/# 限定范围时跳过——用户明确要求仅依据指定文档作答）
    if results and doc_scope is None:
        results = _expand_by_doc_relations(results, user_context)

    # 5.5 检索质量监控日志（学校投入使用后用于诊断与告警）
    # 向量命中率 = 向量命中的去重文档数 / 硬过滤候选数，反映向量通道覆盖率；
    # 持续偏低说明向量库损坏/维度不匹配/Embedding 失效（曾因中文路径导致全失效）。
    vector_hit_docs = len({h.get('doc_id') for h in vector_hits if h.get('doc_id')})
    vector_hit_rate = (vector_hit_docs / len(candidate_ids)) if candidate_ids else 0.0
    if results:
        top3_scores = [round(r.get('score', 0), 3) for r in results[:3]]
        logger.info(
            '[RAG监控] 候选=%d 向量=%d 向量命中率=%.3f 关键词=%d 关键词表=%d 分块=%d 标签=%d Rerank后Top3=%s 返回=%d',
            len(candidate_ids), len(vector_hits), vector_hit_rate,
            len(keyword_hits), len(keyword_table_hits), len(chunk_hits),
            len(tag_hits), top3_scores, len(results),
        )
    else:
        logger.warning(
            '[RAG监控] 零召回 q=%s 候选=%d 向量=%d 向量命中率=%.3f 关键词=%d 关键词表=%d 分块=%d 标签=%d',
            question[:50], len(candidate_ids),
            len(vector_hits), vector_hit_rate, len(keyword_hits),
            len(keyword_table_hits), len(chunk_hits), len(tag_hits),
        )

    # 6. 兜底：向量检索无结果时，按标题/描述关键词搜索（@/# 限定范围时不兜底）
    if not results and doc_scope is None:
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
    min_score: Optional[float] = None,
    rewrite_questions: Optional[List[str]] = None,
    apply_rerank: bool = True,
) -> List[dict]:
    """扩大范围检索：放宽时间/类型/分类过滤，扩大召回并降低最低分。

    供 retrieve() 在首次结果为空或低置信时调用一次；也可单独用于诊断。
    不改动向量库与数据结构。
    """
    if _is_time_question(question):
        return []
    if min_score is None:
        min_score = (
            float(getattr(settings, 'RRF_RETRY_MIN_SCORE', 0.004))
            if fusion_mode() == 'rrf' else RAG_RETRY_MIN_SCORE
        )
    return _retrieve_pipeline(
        question=question,
        user_context=user_context or {'role': 'user'},
        parsed_filters=_widen_retrieval_filters(parsed_filters),
        top_k=top_k,
        min_score=min_score,
        rewrite_questions=rewrite_questions,
        apply_rerank=apply_rerank,
    )


# 子查询召回期间置 True：跳过分解与证据判定，避免递归和重复计费
_IN_DECOMPOSITION = contextvars.ContextVar('rag_in_decomposition', default=False)


def _attach_parent_context(results: List[dict]) -> List[dict]:
    """父子块回填：命中子块后挂上父块全文，供 Prompt 提供完整上下文；

    同时把子块自己的 heading_path（章节路径）从 MySQL 挂上，因为它没存进
    Milvus schema，需要 DB 查询。两个目的：让 build_rag_prompt 优先用
    parent_content + heading_path 组合，比只用子块片段回答更完整。
    """
    pairs = {
        (h.get('doc_id'), h.get('parent_index'))
        for h in results
        if h.get('doc_id') and (h.get('parent_index', -1) or -1) >= 0
    }
    # 子块 heading_path 查询（按 (doc_id, chunk_index) 批量）
    child_pairs = {
        (h.get('doc_id'), h.get('chunk_index'))
        for h in results
        if h.get('doc_id') and h.get('chunk_index') is not None
    }
    if not pairs and not child_pairs:
        return results
    try:
        from apps.knowledge.models import DocChunk
        # 父块：取 content
        parents = {}
        if pairs:
            parents = {
                (row['doc_id'], row['chunk_index']): row['content']
                for row in DocChunk.objects.filter(
                    chunk_level=0, doc_id__in={d for d, _ in pairs},
                ).values('doc_id', 'chunk_index', 'content')
            }
        # 子块：取 heading_path
        child_heads = {}
        if child_pairs:
            child_heads = {
                (row['doc_id'], row['chunk_index']): row['heading_path'] or ''
                for row in DocChunk.objects.filter(
                    chunk_level=1, doc_id__in={d for d, _ in child_pairs},
                ).values('doc_id', 'chunk_index', 'heading_path')
            }
    except Exception as exc:
        logger.warning('父块/heading_path 回填失败（忽略）: %s', type(exc).__name__)
        return results

    for hit in results:
        pid = hit.get('parent_index')
        if pid is not None and pid >= 0:
            text = parents.get((hit.get('doc_id'), pid))
            if text:
                hit['parent_content'] = text
        # heading_path 优先用 hit 里已有的（重打包链路已带上），否则从 DB 补
        if not hit.get('heading_path'):
            head = child_heads.get((hit.get('doc_id'), hit.get('chunk_index')))
            if head:
                hit['heading_path'] = head
    return results


def _merge_subquery_results(groups: List[List[dict]], limit: int, quota: int = 0) -> List[dict]:
    """合并多个子查询的召回结果。

    给定 quota 时先按「每个子问题保底 quota 条」入选，再用全局高分补齐：
    纯按分数合并会让强子问题挤掉弱子问题，恰恰造成"答一半"。
    """
    def _key(hit):
        return hit.get('vector_id') or (hit.get('doc_id'), hit.get('chunk_index'))

    picked: List[dict] = []
    seen = set()
    if quota > 0:
        for hits in groups:
            for hit in sorted(hits, key=lambda h: h.get('score') or 0, reverse=True)[:quota]:
                key = _key(hit)
                if key in seen:
                    continue
                seen.add(key)
                picked.append(hit)

    best: Dict[tuple, dict] = {}
    for hits in groups:
        for hit in hits:
            key = _key(hit)
            if key in seen:
                continue
            prev = best.get(key)
            if prev is None or (hit.get('score') or 0) > (prev.get('score') or 0):
                best[key] = hit
    merged = picked + sorted(best.values(), key=lambda h: h.get('score') or 0, reverse=True)
    merged_len = limit if limit else len(merged)
    # 保底块必须活着：先按分数排保底段，再拼其余，避免在最终截断时被高分噪声挤掉
    picked.sort(key=lambda h: h.get('score') or 0, reverse=True)
    return (picked + merged[len(picked):])[:merged_len]


def _grade_and_maybe_widen(
    question: str,
    results: List[dict],
    *,
    user_context: dict,
    parsed_filters: dict,
    rewrite_questions: list,
    doc_scope: Optional[List[int]],
    attempts: int,
) -> List[dict]:
    """证据充分性判定；不足时扩大检索一次，仍不足则给命中块打标记。"""
    from utils.rag_grader import grade_sufficiency

    grade = grade_sufficiency(question, results, user_context)
    if not grade or grade.get('sufficient'):
        return results

    if (
        doc_scope is None
        and attempts == 0
        and getattr(settings, 'RAG_GRADER_RETRY_ENABLED', True)
    ):
        retry_results = retrieve_wider(
            question=question,
            user_context=user_context,
            parsed_filters=parsed_filters,
            top_k=RAG_RETRY_TOP_K,
            min_score=None,
            rewrite_questions=rewrite_questions,
            apply_rerank=False,
        )
        results = _merge_retry_results(results, retry_results)
        logger.info(
            '[RAG证据判定] 不足(missing=%s) → 扩大检索 %d 条，合并后 %d 条',
            (grade.get('missing') or '')[:60], len(retry_results), len(results),
        )
        grade = grade_sufficiency(question, results, user_context)

    if grade and not grade.get('sufficient'):
        for hit in results:
            hit['evidence_insufficient'] = True
        trace_log(
            'rag_grader', 'sufficient=0 hits=%d missing=%s',
            len(results), (grade.get('missing') or '')[:80],
        )
    return results


def _retrieve_decomposed(
    question: str,
    *,
    user_context: dict,
    parsed_filters: dict,
    top_k: int,
    min_score: Optional[float],
) -> Optional[List[dict]]:
    """分解后逐子问题召回并合并；拆不出有效子问题时返回 None。"""
    from utils.query_decomposer import decompose_question

    sub_questions = decompose_question(question)
    if not sub_questions:
        return None

    per_k = max(8, top_k // len(sub_questions) + 4)
    token = _IN_DECOMPOSITION.set(True)
    groups: List[List[dict]] = []
    try:
        for sub_q in sub_questions:
            groups.append(retrieve(
                sub_q,
                user_context=user_context,
                parsed_filters=parsed_filters,
                top_k=per_k,
                min_score=min_score,
            ))
    finally:
        _IN_DECOMPOSITION.reset(token)

    quota = max(2, top_k // len(sub_questions))
    merged = _merge_subquery_results(groups, top_k * 2, quota=quota)
    logger.info(
        '[RAG查询分解] %d 个子问题 → 各自召回 %s 条，保底%d条/子问题，合并后 %d 条',
        len(sub_questions), [len(g) for g in groups], quota, len(merged),
    )
    trace_log(
        'rag_decompose', 'subs=%d per=%d merged=%d',
        len(sub_questions), per_k, len(merged),
    )
    merged = _grade_and_maybe_widen(
        question, merged,
        user_context=user_context,
        parsed_filters=parsed_filters,
        rewrite_questions=[],
        doc_scope=None,
        attempts=0,
    )
    merged = merged[:top_k] if top_k else merged
    merged = _attach_parent_context(merged)
    from utils.semantic_cache import store as _semantic_store
    _semantic_store(question, user_context, parsed_filters, None, merged)
    return merged


def retrieve(
    question: str,
    user_context: Optional[dict] = None,
    parsed_filters: Optional[dict] = None,
    top_k: int = RAG_TOP_K,
    min_score: Optional[float] = None,
    doc_scope: Optional[List[int]] = None,
) -> List[dict]:
    """
    检索管道（三层检索升级版）：硬过滤 → 双路召回 → 关键词权重 → 权限校验。

    首次结果为空或低置信时，自动扩大范围重试一次（RAG_RETRY_MAX_ATTEMPTS），
    两次结果按现有分数合并去重后返回。doc_scope 非 None 时按用户 @/# 引用的
    文档限定候选范围，此时不做二次检索（范围已由用户显式指定，扩大即越权）。

    Args:
        question: 用户问题
        user_context: 用户上下文 {'user_id': int, 'role': str, 'college_id': int}
        parsed_filters: parse() 输出的过滤条件
        top_k: Milvus 召回数量
        min_score: 最低分阈值（低于此分数不返回）
        doc_scope: @/# 解析后的 doc_id 列表（None 表示不限定范围）

    Returns:
        [{'doc_id': int, 'doc_title': str, 'content': str, 'score': float, 'page': int}, ...]
    """
    user_context = user_context or {'role': 'user'}
    parsed_filters = dict(parsed_filters or {})

    # 阈值按当前融合算法解析：RRF 与加权不重量纲，不能共用 0.30
    if min_score is None:
        min_score = active_score_threshold()

    # 纯时间/日期问题直接跳过检索（由系统注入的「当前时间」回答），
    # 避免命中无关文档、污染参考答案。
    if _is_time_question(question):
        logger.info('纯时间问题，跳过 RAG 检索: %s', question[:30])
        return []

    # 检索级语义缓存（默认关闭）：只复用"证据"，答案仍现生成，引用不会过期
    from utils.semantic_cache import lookup as _semantic_lookup, store as _semantic_store
    cached_hits = _semantic_lookup(question, user_context, parsed_filters, doc_scope)
    if cached_hits is not None:
        floor = min_score or 0.0
        kept = [h for h in cached_hits if (h.get('score') or 0) >= floor] or cached_hits
        return kept[:top_k] if top_k else kept

    # 多目标问句（对比/分别/各学院/历年）：先拆子问题分别召回再合并。
    # @/# 限定范围时不拆，避免把用户显式指定的范围拆散。
    if doc_scope is None and not _IN_DECOMPOSITION.get():
        decomposed = _retrieve_decomposed(
            question,
            user_context=user_context,
            parsed_filters=parsed_filters,
            top_k=top_k,
            min_score=min_score,
        )
        if decomposed is not None:
            return decomposed

    # Query 改写只做一次，首次与重试共用，避免重复调用改写模型。
    from utils.query_rewriter import rewrite_query
    rewrite_questions = rewrite_query(question)
    if len(rewrite_questions) > 1:
        logger.debug('Query 改写: %s -> %s', question[:30], rewrite_questions)

    # 小范围引用（@ 1~3 份文档要求"总结全文"类问题）：放宽分数阈值并加大召回，
    # 保证整篇内容能被拉进上下文，避免 min_score 把正文中段的相关块砍掉。
    if doc_scope is not None and 0 < len(doc_scope) <= 3:
        top_k = top_k * 2
        min_score = 0.0

    results = _retrieve_pipeline(
        question=question,
        user_context=user_context,
        parsed_filters=parsed_filters,
        top_k=top_k,
        min_score=min_score,
        rewrite_questions=rewrite_questions,
        doc_scope=doc_scope,
    )

    # 二次检索：只在首次为空/低置信时触发，次数受 RAG_RETRY_MAX_ATTEMPTS 限制。
    # @/# 限定范围时不重试——范围由用户显式指定，扩大范围违背用户意图。
    attempts = 0
    while doc_scope is None and attempts < RAG_RETRY_MAX_ATTEMPTS:
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
            min_score=None,  # 按融合算法解析重试阈值（weighted=0.15 / rrf 独立）
            rewrite_questions=rewrite_questions,
            apply_rerank=False,  # 重试轮跳过精排，整个请求最多精排一次
        )
        results = _merge_retry_results(results, retry_results)
        logger.info(
            '[RAG二次检索] 第%d次 触发=%s 首次=%d(最高%.3f) 重试=%d 合并=%d',
            attempts, reason, first_count, first_top,
            len(retry_results), len(results),
        )
        trace_log(
            'rag_retry', 'attempt=%d reason=%s first=%d(%.3f) retry=%d merged=%d',
            attempts, reason, first_count, first_top,
            len(retry_results), len(results),
        )
        if not retry_results:
            break

    # 证据充分性判定：分数过线不代表"答得上"，例如问"各学院人数"却只召回制度文件。
    # 子查询召回阶段（_IN_DECOMPOSITION=True）跳过，避免每个子问题各判一次。
    if not _IN_DECOMPOSITION.get():
        results = _grade_and_maybe_widen(
            question, results,
            user_context=user_context,
            parsed_filters=parsed_filters,
            rewrite_questions=rewrite_questions,
            doc_scope=doc_scope,
            attempts=attempts,
        )

    if top_k and len(results) > top_k:
        results = results[:top_k]
    results = _attach_parent_context(results)
    _semantic_store(question, user_context, parsed_filters, doc_scope, results)
    return results
