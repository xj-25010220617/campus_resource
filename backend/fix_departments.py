"""清空并重建 department 表。运行：python manage.py shell -c \"exec(open('fix_departments.py', encoding='utf-8').read())\""""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from apps.classify.models import Department

count = Department.objects.count()
Department.objects.all().delete()
print(f'已删除 department {count} 条')

# 一级部门（ID 1-7）
d1 = [
    (1, '校级领导'), (2, '党政管理部门'), (3, '教学管理部门'),
    (4, '学生工作部门'), (5, '科研与服务部门'), (6, '后勤保障部门'),
    (7, '继续教育与国际教育'),
]
for cid, name in d1:
    Department.objects.create(id=cid, name=name, parent=None, sort_order=cid)
    print(f'  D1: id={cid} {name}')

# 二级部门（ID 从 8 开始）
d2 = [
    ('党委书记', 1), ('校长', 1), ('副校长', 1), ('党委副书记', 1),
    ('党委办公室/校长办公室/纪检监察部', 2), ('党委组织部/人力资源部', 2),
    ('党委宣传部/品牌发展部', 2), ('党群工作部/党委统战部', 2), ('财务资产部', 2),
    ('教务部', 3), ('教学质量管理与保障部', 3), ('实验实训中心', 3),
    ('人文素养教学部', 3),
    ('学生工作部/武装部', 4), ('招生就业工作部', 4),
    ('科研管理部', 5), ('信息中心', 5), ('图书馆', 5),
    ('后勤保障部', 6), ('保卫部', 6),
    ('继续教育（培训）学院/退役军人教育学院', 7),
    ('国际教育学院/国际合作部', 7),
    ('老年教育中心（老年大学）', 7),
]
next_id = 8
for name, parent_id in d2:
    d = Department.objects.create(id=next_id, name=name, parent_id=parent_id, sort_order=next_id)
    print(f'  D2: id={d.id} {d.parent.name} > {d.name}')
    next_id += 1

print(f'\n一级: {Department.objects.filter(parent__isnull=True).count()} 条')
print(f'二级: {Department.objects.filter(parent__isnull=False).count()} 条')
print(f'Total: {Department.objects.count()} 条')
