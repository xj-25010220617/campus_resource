"""种子数据：学科方向 + 资源分类。运行方式：python manage.py shell < seed_data.py"""
from apps.classify.models import Discipline, Category

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
    status = '✓ 新增' if created else '- 已存在'
    print(f'  {status} 学科: {name} ({cat})')
print(f'学科: {Discipline.objects.count()} 条')

# ===========================
# 二、资源分类（category）—— 两级树形
# ===========================
ICON_BASE = 'https://img.alicdn.com/imgextra'

# 一级分类
l1_data = [
    (1, '教学资源', f'{ICON_BASE}/teaching.png', 1),
    (2, '学术资料', f'{ICON_BASE}/academic.png', 2),
    (3, '管理文件', f'{ICON_BASE}/management.png', 3),
    (4, '校园资讯', f'{ICON_BASE}/news.png', 4),
]
for cid, name, icon, order in l1_data:
    obj, created = Category.objects.get_or_create(id=cid, defaults={'name': name, 'icon': icon, 'sort_order': order, 'parent': None})
    status = '✓ 新增' if created else '- 已存在'
    print(f'  {status} 一级分类: {obj.name}')

# 二级分类
l2_data = [
    ('教学课件', 1, 1), ('课程大纲', 1, 2), ('试题库', 1, 3),
    ('实验指导', 1, 4), ('实习指导', 1, 5), ('教材教辅', 1, 6),
    ('论文摘要', 2, 1), ('参考文献', 2, 2), ('毕业设计', 2, 3), ('讲座报告', 2, 4),
    ('规章制度', 3, 1), ('培养方案', 3, 2), ('档案文件', 3, 3), ('政策文件', 3, 4),
    ('通知公告', 4, 1), ('新闻动态', 4, 2),
]
for name, parent_id, order in l2_data:
    obj, created = Category.objects.get_or_create(
        name=name, parent_id=parent_id,
        defaults={'sort_order': order},
    )
    status = '✓ 新增' if created else '- 已存在'
    print(f'  {status} 二级分类: {obj.name} (父: {obj.parent.name})')
print(f'分类: {Category.objects.count()} 条')
print('完成！')
