"""清空并重建 category 表。运行：python manage.py shell -c \"exec(open('fix_categories.py', encoding='utf-8').read())\""""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from apps.classify.models import Category

# ===========================
# 1. 清空全部
# ===========================
count = Category.objects.count()
Category.objects.all().delete()
print(f'已删除 {count} 条')

# ===========================
# 2. 重建一级分类（ID 1-12）
# ===========================
ICON_BASE = 'https://img.alicdn.com/imgextra'
l1 = [
    (1, '行政公文', f'{ICON_BASE}/gov.png'),
    (2, '规章制度', f'{ICON_BASE}/rules.png'),
    (3, '教学建设', f'{ICON_BASE}/teaching.png'),
    (4, '学科建设', f'{ICON_BASE}/discipline.png'),
    (5, '专业建设', f'{ICON_BASE}/major.png'),
    (6, '人事档案', f'{ICON_BASE}/hr.png'),
    (7, '科研学术', f'{ICON_BASE}/research.png'),
    (8, '党群工作', f'{ICON_BASE}/party.png'),
    (9, '财务资产', f'{ICON_BASE}/finance.png'),
    (10, '学生工作', f'{ICON_BASE}/student.png'),
    (11, '上级来文', f'{ICON_BASE}/superior.png'),
    (12, '综合事务', f'{ICON_BASE}/general.png'),
]
for cid, name, icon in l1:
    Category.objects.create(id=cid, name=name, icon=icon, parent=None, sort_order=cid)
    print(f'  L1: id={cid} {name}')

# ===========================
# 3. 重建二级分类（关联正确父级）
# ===========================
l2 = [
    # 行政公文(1)下的二级
    ('通知公告', 1), ('请示批复', 1), ('工作报告', 1), ('会议纪要', 1),
    # 规章制度(2)下的二级
    ('教学管理', 2), ('科研管理', 2), ('人事管理制度', 2),
    # 教学建设(3)下的二级
    ('培养方案', 3), ('课程建设', 3), ('教材建设', 3), ('教学质量', 3), ('实验实训', 3),
    # 学科建设(4)下的二级
    ('学科规划', 4), ('学位点建设', 4), ('重点学科申报', 4), ('学科评估', 4),
    # 专业建设(5)下的二级
    ('专业申报', 5), ('专业认证', 5), ('一流专业建设', 5), ('专业评估', 5),
    # 人事档案(6)下的二级
    ('教师档案', 6), ('职称评聘', 6), ('考核评价', 6), ('人才引进', 6),
    # 科研学术(7)下的二级
    ('项目申报', 7), ('科研成果', 7), ('学术论文', 7), ('学术会议', 7),
    # 党群工作(8)下的二级
    ('党务公文', 8), ('组织建设', 8), ('纪检监察', 8), ('工会团委', 8),
    # 财务资产(9)下的二级
    ('预决算', 9), ('经费管理', 9), ('资产管理', 9), ('审计', 9),
    # 学生工作(10)下的二级
    ('招生就业', 10), ('奖助管理', 10), ('学生活动', 10), ('学籍', 10),
    # 上级来文(11)下的二级
    ('教育部文件', 11), ('省厅文件', 11),
    # 综合事务(12)下的二级
    ('发展规划', 12), ('校园建设', 12), ('信息化', 12), ('安全保卫', 12),
]
next_id = 13
for name, parent_id in l2:
    c = Category.objects.create(id=next_id, name=name, parent_id=parent_id, sort_order=next_id)
    print(f'  L2: id={c.id} {c.parent.name} > {c.name}')
    next_id += 1

# ===========================
# 4. 验证
# ===========================
print(f'\n一级: {Category.objects.filter(parent__isnull=True).count()} 条')
print(f'二级: {Category.objects.filter(parent__isnull=False).count()} 条')
for c in Category.objects.filter(parent__isnull=True):
    children = c.children.values_list('name', flat=True)
    print(f'  [{c.id}] {c.name} → {", ".join(children[:3])}...')
print('\nDone! ID 对照: 1-12=一级, 13-55=二级')
