"""快速创建测试用户脚本（直接连接远程数据库）"""
import os
import sys
import django

# 切换到 backend 目录并设置 Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.accounts.models import User, College
from django.contrib.auth.hashers import make_password

# 查询一个学院（如果没有学院就跳过）
college = College.objects.first()
dept_id = None  # 部门管理员需要 department_id，先试试 None

users_to_create = [
    {
        "username": "test_admin",
        "email": "admin@test.com",
        "password": "Admin123!",
        "role": "super_admin",
        "first_name": "测试",
        "last_name": "管理员",
    },
    {
        "username": "test_college",
        "email": "college@test.com",
        "password": "Admin123!",
        "role": "college_admin",
        "first_name": "测试",
        "last_name": "学院管理员",
        "college": college,
    },
    {
        "username": "test_user",
        "email": "user@test.com",
        "password": "Admin123!",
        "role": "user",
        "first_name": "测试",
        "last_name": "用户",
        "college": college,
    },
]

print(f"连接到数据库... college={college}")
created = []
for u in users_to_create:
    college_field = u.pop("college", None)
    try:
        user, created_flag = User.objects.get_or_create(
            username=u["username"],
            defaults={
                "email": u["email"],
                "password": make_password(u["password"]),
                "role": u["role"],
                "first_name": u["first_name"],
                "last_name": u["last_name"],
                "college": college_field,
            }
        )
        action = "已存在，已更新" if not created_flag else "创建成功"
        if created_flag:
            created.append(user.username)
        print(f"  [{action}] {user.username} | role={user.role} | college={user.college}")
    except Exception as e:
        print(f"  创建失败 {u['username']}: {e}")

print(f"\n本次新建用户: {created}")
