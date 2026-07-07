"""种子数据：学科方向 + 部门。运行方式：python manage.py shell -c \"exec(open('seed_data.py', encoding='utf-8').read())\"

注意：资源分类（Category）系统已移除，不再初始化分类数据。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from apps.classify.models import Discipline, Department

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
# 二、部门（department）—— 两级树形
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
