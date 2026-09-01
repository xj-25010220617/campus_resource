"""创建各角色测试账号（每种身份一个）。

前置：会先补齐 13 个学院 + 两级部门树（幂等，已存在则跳过）。

说明：本脚本用 pymysql 直连数据库（兼容 MySQL 5.7），
      密码哈希用 Django 的 make_password 生成（与后端登录校验兼容）。

运行：python scripts/create_role_accounts.py

角色账号（密码统一 Admin123!）：
- super_admin   超级管理员
- college_admin 学院管理员（绑定学院 1）
- dept_admin    部门管理员（绑定部门 1）
- admin         管理员（兼容旧角色，绑定学院 1）
- student       普通用户/学生（绑定学院 1）
"""
import os
import sys

# 切换到 backend 目录并加载 Django（仅用于 make_password，不连接数据库）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

import pymysql  # noqa: E402
from django.conf import settings  # noqa: E402
from django.contrib.auth.hashers import make_password  # noqa: E402

_db = settings.DATABASES['default']
conn = pymysql.connect(
    host=_db['HOST'], user=_db['USER'], password=_db['PASSWORD'],
    port=int(_db['PORT']), database=_db['NAME'], charset='utf8mb4',
    autocommit=True,
)
cur = conn.cursor()

# ===========================
# 一、学院（13 个）
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
    (9, '基础教学部', 'BED'),
    (10, '马克思主义学院', 'MAR'),
    (11, '创新创业学院', 'IEE'),
    (12, '继续教育学院', 'CED'),
    (13, '退役军人教育学院', 'VET'),
]
cur.executemany(
    'INSERT INTO college (id, name, code, sort_order) VALUES (%s, %s, %s, %s) '
    'ON DUPLICATE KEY UPDATE name=VALUES(name), code=VALUES(code), sort_order=VALUES(sort_order)',
    [(cid, name, code, cid) for cid, name, code in COLLEGES],
)
print(f'学院：已写入/更新 {len(COLLEGES)} 条')

# ===========================
# 二、部门（两级树形）
# ===========================
D1 = [
    (1, '校级领导'), (2, '党政管理部门'), (3, '教学管理部门'),
    (4, '学生工作部门'), (5, '科研与服务部门'), (6, '后勤保障部门'),
    (7, '继续教育与国际教育'),
]
cur.executemany(
    'INSERT INTO department (id, name, sort_order, parent_id) VALUES (%s, %s, %s, NULL) '
    'ON DUPLICATE KEY UPDATE name=VALUES(name), sort_order=VALUES(sort_order), parent_id=NULL',
    [(cid, name, cid) for cid, name in D1],
)

D2 = [
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
_next_id = 8
d2_rows = []
for name, parent_id in D2:
    d2_rows.append((_next_id, name, _next_id, parent_id))
    _next_id += 1
cur.executemany(
    'INSERT INTO department (id, name, sort_order, parent_id) VALUES (%s, %s, %s, %s) '
    'ON DUPLICATE KEY UPDATE name=VALUES(name), sort_order=VALUES(sort_order), parent_id=VALUES(parent_id)',
    d2_rows,
)
print(f'部门：已写入/更新 {len(D1) + len(D2)} 条')

# ===========================
# 三、各角色账号（每种一个）
# ===========================
PASSWORD = 'Admin123!'
_hashed = make_password(PASSWORD)
USERS = [
    # (username, role, college_id, department_id, first_name, last_name)
    ('super_admin', 'super_admin', None, None, '超级', '管理员'),
    ('college_admin', 'college_admin', 1, None, '学院', '管理员'),
    ('dept_admin', 'dept_admin', None, 1, '部门', '管理员'),
    ('admin', 'admin', 1, None, '兼容', '管理员'),
    ('student', 'user', 1, None, '测试', '学生'),
]
for username, role, college_id, dept_id, first, last in USERS:
    cur.execute(
        'INSERT INTO user '
        '(username, password, email, first_name, last_name, role, '
        ' college_id, department_id, is_active, is_staff, is_superuser, date_joined, phone, avatar) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 0, 0, NOW(), %s, %s) '
        'ON DUPLICATE KEY UPDATE '
        'password=VALUES(password), email=VALUES(email), first_name=VALUES(first_name), '
        'last_name=VALUES(last_name), role=VALUES(role), college_id=VALUES(college_id), '
        'department_id=VALUES(department_id), is_active=1',
        (username, _hashed, f'{username}@campus.edu.cn', first, last, role,
         college_id, dept_id, '', ''),
    )
    print(f'账号 {username} (role={role}, college={college_id}, dept={dept_id})')

def _count(table):
    cur.execute(f'SELECT COUNT(*) FROM {table}')
    return cur.fetchone()[0]


user_n = _count('user')
college_n = _count('college')
dept_n = _count('department')

cur.close()
conn.close()
print(f'\n完成。学院 {college_n} | 部门 {dept_n} | 用户 {user_n}')
print(f'所有账号密码统一为：{PASSWORD}')
