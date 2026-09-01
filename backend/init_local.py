# -*- coding: utf-8 -*-
"""本地初始化脚本：学院 + 学科/部门种子数据 + 超级管理员账号。

运行方式：
    cd backend
    python manage.py shell -c "exec(open('init_local.py', encoding='utf-8').read())"
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from django.db import transaction

from apps.accounts.models import College, User

# ===========================
# 一、13 所二级学院（id 与模型注释/FAQ 映射保持一致）
# ===========================
COLLEGES = [
    (1, '计算机与软件学院', 'CS'),
    (2, '智能科学与工程学院', 'ISE'),
    (3, '信息与商务管理学院', 'IBM'),
    (4, '数字艺术与设计学院', 'DAD'),
    (5, '外国语学院', 'FL'),
    (6, '健康医疗科技学院', 'HMT'),
    (7, '高等职业技术学院', 'HVC'),
    (8, '国际教育学院', 'IEC'),
    (9, '基础教学学院', 'BED'),
    (10, '马克思主义学院', 'MAR'),
    (11, '创新创业学院', 'IEE'),
    (12, '继续教育学院', 'CED'),
    (13, '退役军人教育学院', 'VET'),
]

@transaction.atomic
def seed_colleges():
    for cid, name, code in COLLEGES:
        obj, created = College.objects.update_or_create(
            id=cid,
            defaults={'name': name, 'code': code, 'sort_order': cid},
        )
        print(f'  [{"NEW" if created else "OK"}] 学院 {cid}: {name} ({code})')
    print(f'Colleges: {College.objects.count()}')

# ===========================
# 二、学科方向 + 部门（复用 seed_data.py）
# ===========================
def seed_discipline_department():
    import seed_data
    source = open('seed_data.py', encoding='utf-8').read()
    # 去掉前两个 import 语句，避免重复导入 sys / models
    exec(compile(source, 'seed_data.py', 'exec'), globals(), locals())
    print('Discipline/Department seed done.')

# ===========================
# 三、超级管理员账号
# ===========================
def create_super_admin():
    username = 'admin'
    password = 'Admin@123456'
    if User.objects.filter(username=username).exists():
        print(f'  [EXISTS] 用户 {username} 已存在，跳过创建')
        return
    user = User.objects.create_superuser(
        username=username,
        password=password,
        email='admin@neusoft.edu.cn',
        first_name='超级管理员',
    )
    user.role = 'super_admin'
    user.save()
    print(f'  [NEW] 超级管理员 {username} / {password} 创建成功')
    print(f'  role={user.role}, is_staff={user.is_staff}, is_superuser={user.is_superuser}')


print('== 1. 学院 ==')
seed_colleges()
print('== 2. 学科/部门 ==')
seed_discipline_department()
print('== 3. 超级管理员 ==')
create_super_admin()
print('== 初始化完成 ==')
