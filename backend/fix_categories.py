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
# 2. 重建一级分类（ID 1-6）
# ===========================
ICON_BASE = 'https://img.alicdn.com/imgextra'
l1 = [
    (1, '教学资源', f'{ICON_BASE}/teaching.png'),
    (2, '学术资料', f'{ICON_BASE}/academic.png'),
    (3, '管理文件', f'{ICON_BASE}/management.png'),
    (4, '校园资讯', f'{ICON_BASE}/news.png'),
    (5, '校园生活', f'{ICON_BASE}/campus.png'),
    (6, '媒体聚焦', f'{ICON_BASE}/media.png'),
]
for cid, name, icon in l1:
    Category.objects.create(id=cid, name=name, icon=icon, parent=None, sort_order=cid)
    print(f'  L1: id={cid} {name}')

# ===========================
# 3. 重建二级分类（关联正确父级）
# ===========================
l2 = [
    # 教学资源(1)下的二级
    ('教学课件', 1), ('课程大纲', 1), ('试题库', 1),
    ('实验指导', 1), ('实习指导', 1), ('教材教辅', 1),
    # 学术资料(2)下的二级
    ('论文摘要', 2), ('参考文献', 2), ('毕业设计', 2), ('讲座报告', 2),
    # 管理文件(3)下的二级
    ('规章制度', 3), ('培养方案', 3), ('档案文件', 3), ('政策文件', 3),
    # 校园资讯(4)下的二级
    ('通知公告', 4), ('新闻动态', 4), ('学校要闻', 4),
    # 校园生活(5)下的二级
    ('图文东软', 5), ('社团活动', 5), ('东软印记', 5),
    # 媒体聚焦(6)下的二级
    ('媒体报道', 6), ('校友风采', 6),
]
next_id = 7
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
print('\nDone! ID 对照: 1-6=一级, 7-28=二级')
