import os
import sys
sys.path.insert(0, r'E:\github\campus_resource\backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

users = [
    {'username': 'zyr_admin', 'email': 'zyr@test.com', 'password': 'Xj@123456', 'first_name': '张一然'},
    {'username': 'lyq_admin', 'email': 'lyq@test.com', 'password': 'Xj@123456', 'first_name': '李雨琪'},
    {'username': 'test_admin', 'email': 'test@test.com', 'password': 'Xj@123456', 'first_name': '测试管理员'},
]

for u in users:
    obj, created = User.objects.update_or_create(
        username=u['username'],
        defaults={
            'email': u['email'],
            'role': 'super_admin',
            'first_name': u['first_name'],
            'is_active': True,
            'is_staff': True,
            'is_superuser': True,
        }
    )
    obj.set_password(u['password'])
    obj.save()
    print('创建' if created else '更新', u['username'])

print('\n当前 super_admin 列表：')
for u in User.objects.filter(role='super_admin'):
    print(f'  [{u.id}] {u.username} | {u.first_name} | {u.email}')
