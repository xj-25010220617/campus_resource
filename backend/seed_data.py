"""种子数据：学科方向 + 资源分类。运行方式：python manage.py shell -c \"exec(open('seed_data.py', encoding='utf-8').read())\""""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from apps.classify.models import Discipline, Category, Department

# ===========================
# 一、学科方向（discipline）
# ===========================
disciplines = [
    # 工学 — 计算机与软件学院
    ('软件工程', '工学', 1), ('计算机科学与技术', '工学', 2),
    ('网络工程', '工学', 3), ('物联网工程', '工学', 4), ('网络空间安全', '工学', 5),
    # 工学 — 智能科学与工程学院
    ('数据科学与大数据技术', '工学', 6), ('大数据管理与应用', '工学', 7),
    ('机器人工程', '工学', 8), ('信息工程', '工学', 9),
    ('智能科学与技术', '工学', 10), ('人工智能', '工学', 11),
    ('集成电路设计与集成系统', '工学', 12),
    # 管理学 — 信息与商务管理学院
    ('信息管理与信息系统', '管理学', 13), ('供应链管理', '管理学', 14),
    ('人力资源管理', '管理学', 15), ('电子商务', '管理学', 16),
    ('财务管理', '管理学', 17),
    # 艺术学 — 数字艺术与设计学院
    ('数字媒体技术', '艺术学', 18), ('数字媒体艺术', '艺术学', 19),
    ('艺术与科技', '艺术学', 20), ('虚拟现实技术', '艺术学', 21),
    ('工业设计', '艺术学', 22), ('视觉传达设计', '艺术学', 23),
    ('动画', '艺术学', 24),
    # 文学 — 外国语学院
    ('英语', '文学', 25), ('商务英语', '文学', 26), ('日语', '文学', 27),
    # 医学 — 健康医疗科技学院
    ('智能医学工程', '医学', 28), ('医学信息工程', '医学', 29),
    ('医学影像技术', '医学', 30), ('健康服务与管理', '医学', 31),
]

for name, cat, order in disciplines:
    obj, created = Discipline.objects.get_or_create(name=name, defaults={'category': cat, 'sort_order': order})
    status = '[NEW]' if created else '[OK]'
    print(f'  {status} {name} ({cat})')
print(f'Disciplines: {Discipline.objects.count()}')

# ===========================
# 二、资源分类（category）—— 两级树形
# ===========================
ICON_BASE = 'https://img.alicdn.com/imgextra'

# 清理旧数据：删除不在 1-12 范围内的一级分类及其子分类
old_l1_ids = Category.objects.filter(parent=None).exclude(id__in=list(range(1, 13))).values_list('id', flat=True)
if old_l1_ids:
    Category.objects.filter(parent_id__in=old_l1_ids).delete()
    Category.objects.filter(id__in=old_l1_ids).delete()
    print(f'Cleaned old L1 categories: {list(old_l1_ids)}')

# 一级分类（使用 update_or_create 指定 ID）
l1_data = [
    (1, '行政公文', f'{ICON_BASE}/gov.png', 1),
    (2, '规章制度', f'{ICON_BASE}/rules.png', 2),
    (3, '教学建设', f'{ICON_BASE}/teaching.png', 3),
    (4, '学科建设', f'{ICON_BASE}/discipline.png', 4),
    (5, '专业建设', f'{ICON_BASE}/major.png', 5),
    (6, '人事档案', f'{ICON_BASE}/hr.png', 6),
    (7, '科研学术', f'{ICON_BASE}/research.png', 7),
    (8, '党群工作', f'{ICON_BASE}/party.png', 8),
    (9, '财务资产', f'{ICON_BASE}/finance.png', 9),
    (10, '学生工作', f'{ICON_BASE}/student.png', 10),
    (11, '上级来文', f'{ICON_BASE}/superior.png', 11),
    (12, '综合事务', f'{ICON_BASE}/general.png', 12),
]
l1_map = {}  # name → id
for cid, name, icon, order in l1_data:
    obj, created = Category.objects.update_or_create(
        id=cid,
        defaults={'name': name, 'icon': icon, 'sort_order': order, 'parent': None},
    )
    l1_map[name] = obj.id
    status = '[NEW]' if created else '[UPD]'
    print(f'  {status} L1: {obj.name} (id={obj.id})')

# 二级分类
l2_data = [
    # 行政公文
    ('通知公告', '行政公文', 1), ('请示批复', '行政公文', 2),
    ('工作报告', '行政公文', 3), ('会议纪要', '行政公文', 4),
    # 规章制度
    ('教学管理', '规章制度', 1), ('科研管理', '规章制度', 2),
    ('人事管理制度', '规章制度', 3),
    # 教学建设
    ('培养方案', '教学建设', 1), ('课程建设', '教学建设', 2),
    ('教材建设', '教学建设', 3), ('教学质量', '教学建设', 4),
    ('实验实训', '教学建设', 5),
    # 学科建设
    ('学科规划', '学科建设', 1), ('学位点建设', '学科建设', 2),
    ('重点学科申报', '学科建设', 3), ('学科评估', '学科建设', 4),
    # 专业建设
    ('专业申报', '专业建设', 1), ('专业认证', '专业建设', 2),
    ('一流专业建设', '专业建设', 3), ('专业评估', '专业建设', 4),
    # 人事档案
    ('教师档案', '人事档案', 1), ('职称评聘', '人事档案', 2),
    ('考核评价', '人事档案', 3), ('人才引进', '人事档案', 4),
    # 科研学术
    ('项目申报', '科研学术', 1), ('科研成果', '科研学术', 2),
    ('学术论文', '科研学术', 3), ('学术会议', '科研学术', 4),
    # 党群工作
    ('党务公文', '党群工作', 1), ('组织建设', '党群工作', 2),
    ('纪检监察', '党群工作', 3), ('工会团委', '党群工作', 4),
    # 财务资产
    ('预决算', '财务资产', 1), ('经费管理', '财务资产', 2),
    ('资产管理', '财务资产', 3), ('审计', '财务资产', 4),
    # 学生工作
    ('招生就业', '学生工作', 1), ('奖助管理', '学生工作', 2),
    ('学生活动', '学生工作', 3), ('学籍', '学生工作', 4),
    # 上级来文
    ('教育部文件', '上级来文', 1), ('省厅文件', '上级来文', 2),
    # 综合事务
    ('发展规划', '综合事务', 1), ('校园建设', '综合事务', 2),
    ('信息化', '综合事务', 3), ('安全保卫', '综合事务', 4),
]
for name, parent_name, order in l2_data:
    parent_id = l1_map[parent_name]
    obj, created = Category.objects.get_or_create(
        name=name, parent_id=parent_id,
        defaults={'sort_order': order},
    )
    status = '[NEW]' if created else '[OK]'
    print(f'  {status} L2: {obj.name} (parent={parent_name})')
print(f'Categories: {Category.objects.count()}')

# ===========================
# 三、部门（department）—— 两级树形
# ===========================

# 一级部门
d1_data = [
    (1, '校级领导', 1),
    (2, '党政管理部门', 2),
    (3, '教学管理部门', 3),
    (4, '学生工作部门', 4),
    (5, '科研与服务部门', 5),
    (6, '后勤保障部门', 6),
    (7, '继续教育与国际教育', 7),
]
d1_map = {}  # name → id
for cid, name, order in d1_data:
    obj, created = Department.objects.update_or_create(
        id=cid,
        defaults={'name': name, 'sort_order': order, 'parent': None},
    )
    d1_map[name] = obj.id
    status = '[NEW]' if created else '[UPD]'
    print(f'  {status} D1: {obj.name} (id={obj.id})')

# 二级部门
d2_data = [
    # 校级领导
    ('党委书记', '校级领导', 1),
    ('校长', '校级领导', 2),
    ('副校长', '校级领导', 3),
    ('党委副书记', '校级领导', 4),
    # 党政管理部门
    ('党委办公室/校长办公室/纪检监察部', '党政管理部门', 1),
    ('党委组织部/人力资源部', '党政管理部门', 2),
    ('党委宣传部/品牌发展部', '党政管理部门', 3),
    ('党群工作部/党委统战部', '党政管理部门', 4),
    ('财务资产部', '党政管理部门', 5),
    # 教学管理部门
    ('教务部', '教学管理部门', 1),
    ('教学质量管理与保障部', '教学管理部门', 2),
    ('实验实训中心', '教学管理部门', 3),
    ('人文素养教学部', '教学管理部门', 4),
    # 学生工作部门
    ('学生工作部/武装部', '学生工作部门', 1),
    ('招生就业工作部', '学生工作部门', 2),
    # 科研与服务部门
    ('科研管理部', '科研与服务部门', 1),
    ('信息中心', '科研与服务部门', 2),
    ('图书馆', '科研与服务部门', 3),
    # 后勤保障部门
    ('后勤保障部', '后勤保障部门', 1),
    ('保卫部', '后勤保障部门', 2),
    # 继续教育与国际教育
    ('继续教育（培训）学院/退役军人教育学院', '继续教育与国际教育', 1),
    ('国际教育学院/国际合作部', '继续教育与国际教育', 2),
    ('老年教育中心（老年大学）', '继续教育与国际教育', 3),
]
for name, parent_name, order in d2_data:
    parent_id = d1_map[parent_name]
    obj, created = Department.objects.get_or_create(
        name=name, parent_id=parent_id,
        defaults={'sort_order': order},
    )
    status = '[NEW]' if created else '[OK]'
    print(f'  {status} D2: {obj.name} (parent={parent_name})')
print(f'Departments: {Department.objects.count()}')
print('Done!')
