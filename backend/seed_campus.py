"""成都东软学院 — 学校基础数据种子脚本

覆盖：
  1. SchoolProfile (学校概况单例)
  2. College (二级学院 13 个，增强简介/院长/特色/专业列表等)
  3. Discipline (专业 31+ 个，增强所属学院/专业代码/培养层次等)
  4. Scholarship (奖助学金)
  5. Tuition (学费标准)
  6. AdmissionRule (2025 年录取规则)
  7. AcademicCalendar (学期/假期通用安排)

运行：
  cd backend
  python manage.py shell -c "exec(open('seed_campus.py', encoding='utf-8').read())"
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from datetime import date
from decimal import Decimal

from apps.accounts.models import (
    SchoolProfile, College, Scholarship, Tuition, AdmissionRule, AcademicCalendar,
)
from apps.classify.models import Discipline, Department

LINE = "─" * 60


# =============================================================================
# 0. 先跑一遍 seed_data.py 确保 Discipline/Department 基础数据存在
# =============================================================================
print(LINE)
print("0. 确保基础学科/部门数据存在 (seed_data.py)")
exec(open('seed_data.py', encoding='utf-8').read())


# =============================================================================
# 1. SchoolProfile（学校概况单例）
# =============================================================================
print(LINE)
print("1. SchoolProfile 学校概况")
sp_defaults = dict(
    name_full='成都东软学院',
    name_en='Chengdu Neusoft University',
    motto='精勤博学、学以致用',
    president='张应辉',
    party_secretary='张应辉',
    address='四川省成都市都江堰市青城山镇东软大道1号',
    postal_code='611844',
    website='https://www.nsu.edu.cn',
    admission_website='https://zs.nsu.edu.cn',
    admission_phone='028-64888001 / 028-64888002 / 028-64888003',
    admission_email='edu@nsu.edu.cn',
    school_code='4151012636',
    admission_code_sichuan='5177',
    nature='全日制民办普通本科高等院校，由东软集团出资举办',
    level='本科为主，兼有专科，正在筹备硕士研究生教育',
    disciplines_text='工学、管理学、艺术学、文学、医学 5 大学科门类',
    founded_at=2003,
    upgraded_to_bachelor=2011,
    area_m2=Decimal('52.85'),
    building_area_m2=Decimal('36.33'),
    student_count=21000,
    graduate_total=30000,
    major_count=30,
    vocational_major_count=2,
    teacher_count=1076,
    fulltime_teacher_count=815,
    external_teacher_count=398,
    double_qualified_count=137,
    senior_title_count=245,
    phd_master_count=754,
    phd_master_pct=Decimal('92.52'),
    employment_rate=Decimal('94.5'),
    training_model='''TOPCARES 一体化人才培养模式，构建知识、能力、素养八大能力体系。
强调"知识的运用比知识的拥有更重要"。''',
    semester_system='''"1321" 特色学期：1 学年 3 学期（2 个理论学期 + 1 个实践学期）。
三校跨校游学：成都、大连、佛山三地校园。''',
    partner_text='华为、百度、腾讯、京东、阿里巴巴、微软、英特尔、IBM、SAP 等众多知名企业，共建实验室与实训基地。',
    history='''2003 年 — 经教育部批准设立"成都东软信息技术职业学院"，为首批 35 所国家级示范性软件职业技术学院之一。
2008 年 — 汶川大地震中学校距震中仅 19 公里，4600 余名师生分别在大连和佛山异地复课。
2011 年 — 升格为本科院校"成都东软学院"，成为四川省第一所独立设置的民办本科高校。
2012 年 — 设立博士后创新实践基地。
2020 年 — 成立四川省首家"退役军人教育学院"。
2021 年 — 通过教育部普通高等学校本科教学工作合格评估。''',
    mission='''依托东软集团产业背景，面向信息产业与现代服务业，培养具有创新精神与实践能力的高素质应用型人才。''',
)
profile, created = SchoolProfile.objects.update_or_create(id=1, defaults=sp_defaults)
print(f"   [{'NEW' if created else 'UPD'}] SchoolProfile: {profile.name_full}")


# =============================================================================
# 2. College（13 个学院，增强简介/院长/特色/专业列表）
# =============================================================================
print(LINE)
print("2. College 二级学院详情")

COLLEGE_DATA = [
    # id, code, name, intro, dean, website, founded_at, features, partners, specialties_text
    (1, 'CS', '计算机与软件学院',
     '''计算机与软件学院是学校核心学院之一，为四川省首批特色化示范性软件学院。
下设软件工程系、计算机科学与技术系、网络工程系、网络空间安全系。''',
     '', '', None,
     '''软件工程为国家级一流本科专业建设点、一本招生；网络工程、物联网工程为四川省一流专业。
网络安全产业学院为四川省首批现代产业学院（2022 年）。''',
     '华为、百度、阿里、字节跳动、美团、网易、奇安信、科大讯飞、SAP',
     '软件工程、计算机科学与技术（含本硕连读国际班）、网络工程、物联网工程、网络空间安全'),
    (2, 'ISE', '智能科学与工程学院',
     '''原"信息科学与技术学院"，2024 年更现名，聚焦人工智能、大数据、机器人方向。''',
     '', '', None,
     '''省级人工智能实验教学示范中心，下设智能应用、人工智能与并行计算、人工智能创新实验室。
与美国东康涅狄格州立大学合作举办数据科学与大数据技术"3+1"中外合作办学项目。''',
     '东软、百度、京东、格力',
     '信息工程、数据科学与大数据技术、大数据管理与应用、人工智能、智能科学与技术、机器人工程、数据科学(中外合作)'),
    (3, 'IBM', '信息与商务管理学院',
     '''秉承"IT+管理""IT+商务"新工科/新文科融合培养理念，培养兼具信息素养与商业能力的复合型人才。''',
     '', '', None,
     '''依托学校信息学科优势，课程体系融合编程、数据分析、商业管理三大模块。
毕业生就职于马士基、格力、苏宁、用友、金蝶、京东、阿里等企业。''',
     '马士基、格力、苏宁、用友、金蝶、京东、阿里巴巴',
     '信息管理与信息系统、电子商务、财务管理（含本硕连读国际班）、人力资源管理、供应链管理'),
    (4, 'DAD', '数字艺术与设计学院',
     '''前身可追溯至 2005 年成立的数字媒体艺术系，是国家火炬计划成都数字娱乐产业人才培训基地。''',
     '', '', None,
     '''拥有虚拟现实、元宇宙中心、动画电影等 22 个实验实训室和 12 个产学研用工作室。
师生参与制作《流浪地球2》《西游记之再世妖王》等商业电影，历年毕业去向落实率超 94%。''',
     '',
     '数字媒体技术、工业设计、虚拟现实技术、动画、艺术与科技、数字媒体艺术、视觉传达设计'),
    (5, 'FL', '外国语学院',
     '''秉持"外语+IT""外语+商务""外语+艺术"特色培养，毕业生具备跨文化沟通能力与行业应用能力。''',
     '', '', None,
     '''90% 以上教师具有 985/211 及海外留学背景，硕士博士比例达 95%。''',
     '',
     '商务英语、英语、日语'),
    (6, 'HMT', '健康医疗科技学院',
     '''以"IT+健康管理""IT+医疗科技"为特色，培养交叉复合型医学工程技术人才。''',
     '', '', None,
     '''拥有一所三级附属医院和多所三甲教学实习医院。
联合电子科技大学大数据研究中心推出"AI+数据科学"跨学科培养计划。''',
     '电子科技大学大数据研究中心',
     '健康服务与管理、医学影像技术、智能医学工程、医学信息工程'),
    (7, 'HVC', '高等职业技术学院',
     '''学校保留的高职专科教育窗口，面向信息技术与影视动画领域培养一线技能人才。''',
     '', '', None,
     '专科层次 3 年学制，强调动手实训能力。',
     '',
     '软件技术、影视动画（高职专科）'),
    (8, 'IEC', '国际教育学院',
     '''负责学校中外合作办学项目、国际交流与留学生事务。''',
     '', '', None,
     '''与美国、英国、澳大利亚等多国高校开展学分互认、联合培养、短期交流等项目。''',
     '',
     '（中外合作办学相关专业）'),
    (9, 'BED', '基础教学学院',
     '''承担全校通识基础课教学，包括大学英语、大学体育、大学数学等。''',
     '', '', None,
     '',
     '',
     '（承担公共课教学，不直接管理专业）'),
    (10, 'MAR', '马克思主义学院',
     '''承担全校思想政治理论课教学与思想政治教育研究。''',
     '', '', None,
     '',
     '',
     '（承担思政公共课教学，不直接管理专业）'),
    (11, 'IEE', '创新创业学院',
     '''统筹全校创新创业教育，运营 SOVO 国家级众创空间，对接校企创业项目。''',
     '', '', None,
     '',
     '',
     '（创新创业教育，不直接管理专业）'),
    (12, 'CED', '继续教育（培训）学院',
     '''开展成人高等教育、职业技能培训、老年教育等。''',
     '', '', None,
     '''兼管学校"老年教育中心（老年大学）"。''',
     '',
     '（继续教育与培训，不直接管理全日制专业）'),
    (13, 'VET', '退役军人教育学院',
     '''四川省首家面向退役军人开展全日制学历教育的二级学院（2020 年成立）。''',
     '', '', 2020,
     '',
     '',
     '（退役军人专升本/专项培养）'),
]

for cid, code, name, intro, dean, website, founded, features, partners, majors in COLLEGE_DATA:
    obj, created = College.objects.update_or_create(
        id=cid,
        defaults=dict(
            code=code, name=name, sort_order=cid,
            intro=intro, dean=dean, website=website or '',
            founded_at=founded, features=features, partners=partners,
            specialties_text=majors,
        ),
    )
    status = '[NEW]' if created else '[UPD]'
    print(f"   {status} College: {obj.id} {obj.name} ({obj.code}) 专业快照={len(majors)}字")


# =============================================================================
# 3. Discipline（专业 — 建立学院关联 + 补充专业详情）
# =============================================================================
print(LINE)
print("3. Discipline 专业详情（建立学院关联）")

# 学院 → 专业名称映射（用于 FK 关联）
COLLEGE_MAJORS = {
    '计算机与软件学院': [
        # (专业名称, 专业代码, 层次, 学制, 一流专业, 一流类型, 招生批次)
        ('软件工程', '080902', 'bachelor', '4年', True, '国家级', '一本'),
        ('计算机科学与技术', '080901', 'bachelor', '4年', True, '省级', '二本(部分省份一本)'),
        ('网络工程', '080903', 'bachelor', '4年', True, '省级', '二本'),
        ('物联网工程', '080905', 'bachelor', '4年', True, '省级', '二本'),
        ('网络空间安全', '080911', 'bachelor', '4年', False, '', '二本'),
    ],
    '智能科学与工程学院': [
        ('信息工程', '080706', 'bachelor', '4年', False, '', '二本'),
        ('数据科学与大数据技术', '080910', 'bachelor', '4年', False, '', '二本'),
        ('大数据管理与应用', '120108T', 'bachelor', '4年', False, '', '二本'),
        ('人工智能', '080907T', 'bachelor', '4年', False, '', '二本'),
        ('智能科学与技术', '080906T', 'bachelor', '4年', False, '', '二本'),
        ('机器人工程', '080803T', 'bachelor', '4年', False, '', '二本'),
    ],
    '信息与商务管理学院': [
        ('信息管理与信息系统', '120102', 'bachelor', '4年', False, '', '二本'),
        ('电子商务', '120801', 'bachelor', '4年', False, '', '二本'),
        ('财务管理', '120204', 'bachelor', '4年', False, '', '二本'),
        ('人力资源管理', '120206', 'bachelor', '4年', False, '', '二本'),
        ('供应链管理', '120601T', 'bachelor', '4年', False, '', '二本'),
    ],
    '数字艺术与设计学院': [
        ('数字媒体技术', '080912T', 'bachelor', '4年', False, '', '二本'),
        ('工业设计', '080205', 'bachelor', '4年', False, '', '二本'),
        ('虚拟现实技术', '080913T', 'bachelor', '4年', False, '', '二本'),
        ('动画', '130310', 'bachelor', '4年', False, '', '二本'),
        ('艺术与科技', '130509T', 'bachelor', '4年', False, '', '二本'),
        ('数字媒体艺术', '130508', 'bachelor', '4年', False, '', '二本'),
        ('视觉传达设计', '130502', 'bachelor', '4年', False, '', '二本'),
    ],
    '外国语学院': [
        ('商务英语', '050262', 'bachelor', '4年', False, '', '二本'),
        ('英语', '050201', 'bachelor', '4年', False, '', '二本'),
        ('日语', '050207', 'bachelor', '4年', False, '', '二本'),
    ],
    '健康医疗科技学院': [
        ('健康服务与管理', '101101T', 'bachelor', '4年', False, '', '二本'),
        ('医学影像技术', '101003', 'bachelor', '4年', False, '', '二本'),
        ('智能医学工程', '101010T', 'bachelor', '4年', False, '', '二本'),
        ('医学信息工程', '101005', 'bachelor', '4年', False, '', '二本'),
    ],
    '高等职业技术学院': [
        ('软件技术', '510203', 'vocational', '3年', False, '', '专科'),
        ('影视动画', '510215', 'vocational', '3年', False, '', '专科'),
    ],
}

updated = 0
for college_name, major_list in COLLEGE_MAJORS.items():
    try:
        college = College.objects.get(name=college_name)
    except College.DoesNotExist:
        print(f"   [SKIP] College {college_name} 不存在")
        continue
    for major_name, code, level, duration, is_first, specialty_type, batch in major_list:
        try:
            d = Discipline.objects.get(name=major_name)
            d.college = college
            d.major_code = code
            d.level = level
            d.duration = duration
            d.is_first_class = is_first
            d.specialty_type = specialty_type if is_first else ''
            d.admission_batch = batch
            d.save()
            updated += 1
            print(f"   [UPD] Discipline: {d.name} → {college.name} ({level})")
        except Discipline.DoesNotExist:
            print(f"   [NEW-SKIP] Discipline {major_name} 不存在，seed_data.py 里没定义")

print(f"   Discipline 已更新学院关联: {updated} / {Discipline.objects.count()}")


# =============================================================================
# 4. Scholarship（奖助学金）
# =============================================================================
print(LINE)
print("4. Scholarship 奖助学金")

SCHOLARSHIPS = [
    # (name, type, level, amount, description, sort)
    ('华天奖学金', 'scholarship', 'enterprise', '10000 元/人·年',
     '成都东软学院最高荣誉奖学金，由学校董事长华天设立，每年评选。', 1),
    ('东软睿新奖学金·一等奖', 'scholarship', 'enterprise', '5000 元/人·年',
     '东软集团设立，激励学业优秀学生。', 2),
    ('东软睿新奖学金·二等奖', 'scholarship', 'enterprise', '2000 元/人·年', '', 3),
    ('东软睿新奖学金·三等奖', 'scholarship', 'enterprise', '1000 元/人·年', '', 4),
    ('国家奖学金', 'scholarship', 'national', '10000 元/人·年',
     '面向二年级及以上全日制本科生，每学年评选一次，比例约 0.3%。', 5),
    ('国家励志奖学金', 'scholarship', 'national', '6000 元/人·年',
     '面向品学兼优的家庭经济困难学生，每学年评选。', 6),
    ('国家助学金·一等', 'grant', 'national', '4900 元/人·年',
     '家庭经济困难学生分档资助。', 7),
    ('国家助学金·二等', 'grant', 'national', '3300 元/人·年', '', 8),
    ('国家助学金·三等', 'grant', 'national', '2500 元/人·年', '', 9),
    ('生源地信用助学贷款', 'loan', 'national', '最高 20000 元/年',
     '在校期间国家贴息，毕业后按约定利率还款。', 10),
]

for name, stype, level, amount, desc, sort in SCHOLARSHIPS:
    obj, created = Scholarship.objects.update_or_create(
        name=name,
        defaults=dict(type=stype, level=level, amount=amount, description=desc, sort_order=sort),
    )
    status = '[NEW]' if created else '[UPD]'
    print(f"   {status} Scholarship: {obj.name} ({obj.get_level_display()}) {obj.amount}")


# =============================================================================
# 5. Tuition（学费标准）
# =============================================================================
print(LINE)
print("5. Tuition 学费标准")

TUITIONS = [
    ('普通本科专业', '18000～20000 元/年', '按专业类别收费，文理类偏低、工科类偏高。'),
    ('中外合作办学（前三年）', '68000 元/年', '后两年按对应本科专业标准或转入合作高校收费。'),
    ('艺术类本科专业', '20000 元/年', '含数字媒体艺术、视觉传达设计、动画等。'),
    ('高职专科专业', '按四川省发改委核定', '软件技术、影视动画（高职）另见省发改委批复。'),
]

for cat, amount, desc in TUITIONS:
    obj, created = Tuition.objects.update_or_create(
        category=cat,
        defaults=dict(amount_text=amount, description=desc),
    )
    status = '[NEW]' if created else '[UPD]'
    print(f"   {status} Tuition: {obj.category} — {obj.amount_text}")


# =============================================================================
# 6. AdmissionRule（2025 年录取规则）
# =============================================================================
print(LINE)
print("6. AdmissionRule 2025 年录取规则")

content_2025 = '''1. 实行平行志愿投档省份：按投档考生的投档成绩从高到低、分数优先的原则录取，不设专业级差。
2. 调档比例：平行志愿批次调档比例 100%。
3. 加(降)分政策：认可国家和各省(自治区、直辖市)规定的加(降)分政策。
4. 外语语种：不限，非外语专业公共必修课为大学英语。
5. 男女比例：不限。
6. 体检要求：按教育部、卫生部、中国残疾人联合会印发的《普通高等学校招生体检工作指导意见》执行。'''

exam_req = '''按教育部、卫生部、中国残疾人联合会印发的《普通高等学校招生体检工作指导意见》执行。
新生入学后，学校在 3 个月内按照国家招生规定进行复查，包括录取手续及程序、录取资格、身份证明、身心健康状况、专业水平等。复查不合格者按相关规定处理。'''

transfer = '''新生入校后可按学校规定申请转专业，具体条件与流程见教务部每学年发布的《转专业管理办法》。'''

obj, created = AdmissionRule.objects.update_or_create(
    year=2025,
    defaults=dict(content=content_2025, exam_requirement=exam_req, transfer_policy=transfer),
)
print(f"   [{'NEW' if created else 'UPD'}] AdmissionRule: {obj}")


# =============================================================================
# 7. AcademicCalendar（学期/假期通用描述）
# =============================================================================
print(LINE)
print("7. AcademicCalendar 学期校历")

CALENDAR_GENERIC = [
    ('每学年第一学期（秋季）', 'theory', None, None, '9 月初开学，1 月中下旬结束；期末约 1 月中旬。', 1),
    ('每学年第二学期（春季）', 'theory', None, None, '2 月底-3 月初开学，6-7 月结束。', 2),
    ('实践学期（暑期）', 'practice', None, None, '"1321" 体系中的实践学期，安排在夏季。', 3),
    ('寒假', 'winter', None, None, '约 5 周：1 月中旬至 2 月底。', 4),
    ('暑假', 'summer', None, None, '约 6 周：7 月中旬至 8 月底。', 5),
    ('军训', 'military', None, None, '新生开学即训，时长约 2 周。', 6),
    ('毕业典礼', 'graduation', None, None, '每年 6 月中下旬举行。', 7),
]

for name, ttype, start, end, note, sort in CALENDAR_GENERIC:
    obj, created = AcademicCalendar.objects.update_or_create(
        term_name=name,
        defaults=dict(term_type=ttype, start_date=start, end_date=end, note=note, sort_order=sort),
    )
    status = '[NEW]' if created else '[UPD]'
    print(f"   {status} AcademicCalendar: {obj.term_name}")


# =============================================================================
# 总结
# =============================================================================
print(LINE)
print()
print("=== 种子数据加载完成 ===")
print(f"  SchoolProfile        : {SchoolProfile.objects.count()} 条")
print(f"  College  (增强后)    : {College.objects.count()} 条")
print(f"  Discipline (已关联学院): {Discipline.objects.filter(college__isnull=False).count()} / {Discipline.objects.count()}")
print(f"  Scholarship          : {Scholarship.objects.count()} 条")
print(f"  Tuition              : {Tuition.objects.count()} 条")
print(f"  AdmissionRule        : {AdmissionRule.objects.count()} 条")
print(f"  AcademicCalendar     : {AcademicCalendar.objects.count()} 条")
print(LINE)
