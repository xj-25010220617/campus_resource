"""
同义词词典与关键词扩展

将用户口语化/缩略词/拼音映射为数据库中的标准术语，
提升 RAG 检索命中率。

维护方式：
- 代码内 SYNONYM_DICT 手工维护（高频映射）
- 可选外部 JSON 自定义词典（通过 SYNONYM_DICT_CUSTOM_PATH 配置）
"""
import json
import logging
import os
from typing import Dict, List, Set

from django.conf import settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 同义词词典：口语/缩略词 → 标准术语
# ------------------------------------------------------------------
SYNONYM_DICT: Dict[str, List[str]] = {
    # ── 专业/课程缩略 ──
    '计科': ['计算机科学与技术', '计算机'],
    '大物': ['大学物理'],
    '高数': ['高等数学'],
    '马原': ['马克思主义基本原理'],
    '毛概': ['毛泽东思想和中国特色社会主义理论体系概论'],
    '思修': ['思想道德修养与法律基础'],
    '软工': ['软件工程'],
    '网工': ['网络工程'],
    '数媒': ['数字媒体技术', '数字媒体艺术'],
    '电商': ['电子商务'],
    '信管': ['信息管理与信息系统'],
    '大数据': ['数据科学与大数据技术'],
    '物联网': ['物联网工程'],
    'AI': ['人工智能'],
    'VR': ['虚拟现实'],
    '计网': ['计算机网络'],
    '数电': ['数字电子技术'],
    '模电': ['模拟电子技术'],
    '微机': ['微机原理', '微型计算机原理'],
    '编译': ['编译原理'],
    'DS': ['数据结构'],
    'OS': ['操作系统'],
    'DB': ['数据库'],
    # ── 学院/组织缩略 ──
    '计院': ['计算机与软件学院'],
    '智院': ['智能科学与工程学院'],
    '商管': ['信息与商务管理学院'],
    '艺院': ['数字艺术与设计学院'],
    '外院': ['外国语学院'],
    '康院': ['健康医疗科技学院'],
    '马院': ['马克思主义学院'],
    '基础部': ['通识教学部'],
    # ── 学校相关 ──
    '东软': ['成都东软学院', '东软学院', '成都东软'],
    '东软学院': ['成都东软学院'],
    'neusoft': ['成都东软学院', '东软'],
    # ── 课程术语 ──
    '课设': ['课程设计'],
    '毕设': ['毕业设计'],
    '实验报告': ['实验指导'],
    '题库': ['试题库'],
    '课件': ['教学课件', '课件'],
    '教案': ['教学课件'],
    'PPT': ['幻灯片', '演示文稿'],
    '大纲': ['培养方案', '教学大纲'],
    # ── 常见搜索词扩展 ──
    '考研': ['研究生', '硕士'],
    '保研': ['推免', '保送研究生'],
    '四级': ['英语四级', 'CET-4'],
    '六级': ['英语六级', 'CET-6'],
    '奖学金': ['奖助学金', '国家奖学金', '国家励志奖学金'],
    '社团': ['学生社团', '学生会'],
    '寝室': ['宿舍', '学生公寓'],
    '食堂': ['餐厅', '饮食'],
    '选课': ['课程选修', '选修课'],
    '重修': ['补考'],
    '转专业': ['专业调整'],
}

# ------------------------------------------------------------------
# 拼音容错映射（仅常见缩写，全拼请用自定义词典）
# ------------------------------------------------------------------
PINYIN_MAP: Dict[str, str] = {
    'jisuanji': '计算机',
    'ruanjian': '软件',
    'wangluo': '网络',
    'shujuku': '数据库',
    'caozuoxitong': '操作系统',
    'biancheng': '编程',
    'rengongzhineng': '人工智能',
    'wulianwang': '物联网',
    'daxuesheng': '大学生',
    'xuexiao': '学校',
    'jiaoshi': '教师',
    'xuesheng': '学生',
    'kaoshi': '考试',
    'chengji': '成绩',
    'jiangxuejin': '奖学金',
    'sushe': '宿舍',
    'shitang': '食堂',
}

# ------------------------------------------------------------------
# 加载自定义词典
# ------------------------------------------------------------------
def _load_custom_synonyms() -> Dict[str, List[str]]:
    """从外部 JSON 文件加载自定义同义词（失败静默跳过）。"""
    custom_path = getattr(settings, 'SYNONYM_DICT_CUSTOM_PATH', '')
    if not custom_path:
        return {}
    if not os.path.exists(custom_path):
        logger.debug('同义词自定义词典不存在: %s', custom_path)
        return {}
    try:
        with open(custom_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            logger.info('加载自定义同义词词典: %s (%d 条)', custom_path, len(data))
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning('加载自定义同义词词典失败: %s - %s', custom_path, e)
    return {}


# 合并内置 + 自定义词典（模块加载时执行一次）
_CUSTOM = _load_custom_synonyms()
if _CUSTOM:
    SYNONYM_DICT.update(_CUSTOM)


# ------------------------------------------------------------------
# 公共接口
# ------------------------------------------------------------------
def expand_keywords(keywords: List[str]) -> List[str]:
    """
    对关键词列表做同义词扩展和拼音容错。

    Args:
        keywords: 分词后的关键词列表（如 jieba.lcut 输出）

    Returns:
        扩展后的关键词列表（含原词 + 同义词），去重
    """
    if not keywords:
        return []

    expanded: Set[str] = set()

    for kw in keywords:
        # 保留原词
        expanded.add(kw)

        # 同义词扩展
        kw_lower = kw.lower()
        if kw_lower in SYNONYM_DICT:
            for synonym in SYNONYM_DICT[kw_lower]:
                expanded.add(synonym)

        # 拼音容错
        if kw_lower in PINYIN_MAP:
            expanded.add(PINYIN_MAP[kw_lower])

        # 拆解混合词（如 "计科2025" → "计科" + "2025"）
        import re as _re
        parts = _re.findall(r'[a-zA-Z]+|[一-鿿]+|\d+', kw)
        for part in parts:
            if part != kw:
                part_lower = part.lower()
                if part_lower in SYNONYM_DICT:
                    for synonym in SYNONYM_DICT[part_lower]:
                        expanded.add(synonym)
                if part_lower in PINYIN_MAP:
                    expanded.add(PINYIN_MAP[part_lower])

    return list(expanded)
