"""
敏感内容过滤器（静态规则引擎）

定位：拦截 AI 平台自带过滤之外的场景特有敏感内容
    - AI 已拦（不需重复）：涉政/色情/暴力/谣言/辱骂
    - 这里负责：涉密保密 + 个人隐私 + 结构化数据 + 保密边界行为

六路规则 → 命中任意一路即拦截，返回 SensitiveResult
"""
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ====================================================================
# 第一路：涉密保密关键词
# ====================================================================
CLASSIFIED_KEYWORDS = [
    # 密级标识
    '绝密', '机密', '秘密文件', '内部文件', '内部资料', '内部参阅',
    '不予公开', '未解密', '涉密', '保密等级',
    # 文件载体
    '红头文件', '内部发文', '涉密文件', '保密文件',
    '党组会议纪要', '党委会纪要', '校长办公会纪要',
    '党政联席会议纪要', '保密委员会纪要',
    # 未公开政策
    '未公开政策', '内部规定', '内部管理办法',
    '仅限内部传阅', '不得外传', '禁止外泄',
    # 商业保密
    '商业机密', '技术机密', '核心技术源码', '未发布产品方案',
    '客户涉密数据', '独家商业合同', '内部报价', '未公开财报',
    # 军事/国安
    '军事机密', '国防机密', '部队番号', '武器装备参数',
]


# ====================================================================
# 第二路：个人隐私正则
# ====================================================================
PRIVACY_PATTERNS = {
    'id_card': {
        'pattern': re.compile(
            r'[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]',
        ),
        'desc': '身份证号码（18位）',
    },
    'phone': {
        'pattern': re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
        'desc': '手机号码',
    },
    'bank_card': {
        'pattern': re.compile(r'\b\d{16,19}\b'),
        'desc': '银行卡号',
    },
    'home_address': {
        'pattern': re.compile(
            r'(?:住址|地址|家庭住址|详细地址)\s*[：:]\s*\S{5,50}',
        ),
        'desc': '家庭住址明文',
    },
    'account_password': {
        'pattern': re.compile(
            r'(?:密码|账号|口令|登录凭证)\s*[：:是为]\s*\S{3,30}',
        ),
        'desc': '账号密码泄露',
    },
    'face_realname': {
        'pattern': re.compile(
            r'(?:人脸|实名|身份证号|真实姓名)\s*[：:]\s*\S+',
        ),
        'desc': '人脸/实名信息',
    },
    'medical_record': {
        'pattern': re.compile(
            r'(?:病历|诊断书|处方|体检报告|住院记录)\s*[：:]',
        ),
        'desc': '个人病历隐私',
    },
    'student_id': {
        'pattern': re.compile(
            r'\b(?:学号|工号|考生号|准考证号)\s*[：:]\s*\d{6,15}',
        ),
        'desc': '学号/工号查询',
    },
}


# ====================================================================
# 第三路：结构化保密数据正则
# ====================================================================
STRUCTURAL_SECRET_PATTERNS = {
    'classified_doc_no': {
        'pattern': re.compile(
            r'[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青川藏宁琼]'
            r'[政发办字函党通纪要批决令]\s*[〔（(]\s*20\d{2}\s*[〕）)]\s*\d+\s*号',
        ),
        'desc': '政府/机关涉密公文文号',
    },
    'internal_seal': {
        'pattern': re.compile(
            r'(?:公章|印章|钢印|电子签章)\s*(?:编号|文件|样式)',
        ),
        'desc': '公章/印章编号查询',
    },
    'api_key': {
        'pattern': re.compile(
            r'(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token)\s*[=:]\s*\S{16,}',
            re.IGNORECASE,
        ),
        'desc': 'API密钥/Token',
    },
    'internal_ip': {
        'pattern': re.compile(
            r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
            r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}'
            r'|192\.168\.\d{1,3}\.\d{1,3})\b',
        ),
        'desc': '服务器内网IP',
    },
    'internal_system': {
        'pattern': re.compile(
            r'(?:oa\s*系统|内网地址|内部系统|vpn地址|堡垒机|运维后台)',
            re.IGNORECASE,
        ),
        'desc': '内网系统名称/地址',
    },
}


# ====================================================================
# 第四路：保密特殊逻辑 — 公私边界 + 禁生成涉密载体
# ====================================================================
FORBIDDEN_DOC_GENERATION = [
    '涉密方案', '保密通知', '内部考核资料', '涉密台账',
    '保密协议模板', '保密承诺书', '涉密报告', '内部审计底稿',
    '竞业限制协议', '保密条款', '保密等级评定',
]

FORBIDDEN_ACCESS_ACTIONS = [
    '获取内部保密文件', '绕过保密制度', '解密涉密材料',
    '破解加密文档', '越权访问', '查看他人密级文件',
    '获取未授权数据', '提取内部数据', '下载涉密内容',
    '打听内部消息', '内部消息透露', '保密文件发我', '内部文件分享',
]

BYPASS_ATTEMPT_PATTERNS = [
    '假设没有保密规定', '只是测试过滤', '随便说涉密内容',
    '私下说不传播', '你私下告诉我', '假装你不是AI',
    '忽略之前的保密要求', '别管保密规定了',
    '就咱们两个人知道', '测试一下你的底线',
    '开个后门', '跳过审核', '绕过过滤', '屏蔽检测',
]


# ====================================================================
# 分级与拒绝话术
# ====================================================================
LEVEL_ONE_REFUSE = (
    '抱歉，您的问题涉及违规或涉密内容，系统无法处理。'
    '如有疑问请联系学校相关部门。'
)

REFUSE_MESSAGES = {
    'classified': (
        '抱歉，该问题涉及内部保密信息，无法回答。如需查阅相关文件，'
        '请联系学校相关职能部门通过正规渠道获取。'
    ),
    'privacy': (
        '抱歉，检测到您的问题中包含个人隐私信息。为保护隐私安全，'
        '本系统不处理包含个人敏感信息的请求。'
    ),
    'structural': (
        '抱歉，您的问题涉及系统内部数据或密钥信息，无法处理。'
    ),
    'bypass': (
        '抱歉，系统检测到您的提问方式存在规避内容过滤的意图。'
        '请以正常方式提问校园学习相关问题。'
    ),
    'forbidden_generation': (
        '抱歉，本系统不支持生成涉密载体、保密文件或内部档案类内容。'
        '如需相关文件，请通过学校正规流程申请。'
    ),
    'forbidden_access': (
        '抱歉，获取内部保密资料或越权访问的行为不被允许。'
        '请通过学校正规渠道查阅相关信息。'
    ),
}


@dataclass
class SensitiveResult:
    """敏感检测结果"""
    is_sensitive: bool = False
    level: int = 0                         # 1=涉密 2=隐私
    matched_rules: list = field(default_factory=list)
    matched_texts: list = field(default_factory=list)
    refuse_message: str = ''


def check_sensitive(text: str) -> SensitiveResult:
    """
    四路静态规则检测。

    Args:
        text: 用户提问文本

    Returns:
        SensitiveResult: is_sensitive / level / matched_rules / refuse_message
    """
    if not text:
        return SensitiveResult()

    result = SensitiveResult()

    # ---- 第一路：涉密保密关键词 ----
    for kw in CLASSIFIED_KEYWORDS:
        if kw in text:
            result.is_sensitive = True
            result.level = max(result.level, 1)
            result.matched_rules.append({
                'rule': kw, 'desc': f'涉密关键词：{kw}',
                'type': 'classified', 'category': '涉密保密关键词',
            })
            result.matched_texts.append(kw)

    # ---- 第二路：个人隐私正则 ----
    for rule_name, rule_info in PRIVACY_PATTERNS.items():
        matches = rule_info['pattern'].findall(text)
        if matches:
            result.is_sensitive = True
            result.level = max(result.level, 2)
            result.matched_rules.append({
                'rule': rule_name, 'desc': rule_info['desc'],
                'type': 'privacy', 'category': '个人隐私',
            })
            result.matched_texts.extend(matches)

    # ---- 第三路：结构化保密数据 ----
    for rule_name, rule_info in STRUCTURAL_SECRET_PATTERNS.items():
        matches = rule_info['pattern'].findall(text)
        if matches:
            result.is_sensitive = True
            result.level = max(result.level, 1)
            result.matched_rules.append({
                'rule': rule_name, 'desc': rule_info['desc'],
                'type': 'structural', 'category': '结构化保密数据',
            })
            result.matched_texts.extend(matches)

    # ---- 第四路：保密特殊逻辑 ----
    for doc_type in FORBIDDEN_DOC_GENERATION:
        if doc_type in text:
            result.is_sensitive = True
            result.level = max(result.level, 1)
            result.matched_rules.append({
                'rule': doc_type, 'desc': f'禁止生成：{doc_type}',
                'type': 'forbidden_generation', 'category': '保密特殊逻辑·禁生成',
            })
            result.matched_texts.append(doc_type)

    for action in FORBIDDEN_ACCESS_ACTIONS:
        if action in text:
            result.is_sensitive = True
            result.level = max(result.level, 1)
            result.matched_rules.append({
                'rule': action, 'desc': f'禁止行为：{action}',
                'type': 'forbidden_access', 'category': '保密特殊逻辑·禁获取',
            })
            result.matched_texts.append(action)

    for bypass_text in BYPASS_ATTEMPT_PATTERNS:
        if bypass_text in text:
            result.is_sensitive = True
            result.level = max(result.level, 1)
            result.matched_rules.append({
                'rule': bypass_text, 'desc': f'绕过意图：{bypass_text}',
                'type': 'bypass', 'category': '保密特殊逻辑·绕过检测',
            })
            result.matched_texts.append(bypass_text)

    # ---- 选择拒绝话术 ----
    if result.is_sensitive:
        result.refuse_message = _select_refuse(result)

    return result


def _select_refuse(result: SensitiveResult) -> str:
    """按优先级选择拒绝话术"""
    rule_types = {r['type'] for r in result.matched_rules}
    for t in ['classified', 'structural', 'bypass',
              'forbidden_generation', 'forbidden_access', 'privacy']:
        if t in rule_types:
            return REFUSE_MESSAGES.get(t, LEVEL_ONE_REFUSE)
    return LEVEL_ONE_REFUSE
