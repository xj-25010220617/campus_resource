"""检查数据库连接 + admin_operation_log / admin_login_log 表是否就位 + 关键 model 字段是否对得上"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.db import connection

# 1. 测连接
print('=' * 60)
print('1. 数据库连接测试')
print('=' * 60)
try:
    connection.ensure_connection()
    with connection.cursor() as c:
        c.execute('SELECT VERSION()')
        v = c.fetchone()
        print(f'  OK -> MySQL {v[0]}')
        c.execute('SELECT DATABASE()')
        print(f'  当前 DB -> {c.fetchone()[0]}')
except Exception as e:
    print(f'  FAIL -> {type(e).__name__}: {str(e)[:300]}')
    sys.exit(1)

# 2. 表是否存在
print()
print('=' * 60)
print('2. 关键审计表存在性检查')
print('=' * 60)
for tbl in ['admin_operation_log', 'admin_login_log']:
    try:
        with connection.cursor() as c:
            c.execute(
                "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_COMMENT "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                [tbl],
            )
            row = c.fetchone()
            if row:
                print(f'  [OK] {tbl:25s}  engine={row[1]}  collation={row[2]}')
                print(f'        comment={row[3]}')
            else:
                print(f'  [MISSING] {tbl}  ← 表不存在，model managed=False 会直接报错')
    except Exception as e:
        print(f'  [ERROR] {tbl}: {e}')

# 3. admin_operation_log 字段对得上 model 吗？
print()
print('=' * 60)
print('3. admin_operation_log 字段 vs Model 字段一致性')
print('=' * 60)
expected = {
    'id': 'bigint',
    'operator_id': 'bigint',
    'operator_username': 'varchar',
    'action': 'varchar',
    'target_type': 'varchar',
    'target_id': 'bigint',
    'target_repr': 'varchar',
    'after': 'json',
    'ip_address': 'varchar',
    'user_agent': 'varchar',
    'created_at': 'datetime',
}
try:
    with connection.cursor() as c:
        c.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'admin_operation_log' "
            "ORDER BY ORDINAL_POSITION"
        )
        cols = {row[0]: row for row in c.fetchall()}
    missing = [k for k in expected if k not in cols]
    extra = [k for k in cols if k not in expected]
    print(f'  表里实际字段: {list(cols.keys())}')
    print(f'  Model 期望字段: {list(expected.keys())}')
    if missing:
        print(f'  [FAIL] 表缺少字段: {missing}  ← 写入时会报错')
    if extra:
        print(f'  [INFO] 表比 model 多出字段: {extra}  ← 一般无害')
    if not missing and not extra:
        print('  [OK] 字段完全一致')
    # 索引检查
    with connection.cursor() as c:
        c.execute(
            "SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'admin_operation_log' "
            "GROUP BY INDEX_NAME ORDER BY INDEX_NAME"
        )
        idxs = c.fetchall()
    print(f'  索引:')
    for n, cols_str in idxs:
        print(f'    {n}: ({cols_str})')
except Exception as e:
    print(f'  [ERROR] {e}')

# 4. 实际写入一条 + 读出来（验证 ORM 写入链）
print()
print('=' * 60)
print('4. 模拟一次 write_operation_log 完整链路')
print('=' * 60)
try:
    from apps.admin_panel.models import OperationLog
    from apps.admin_panel.utils import write_operation_log
    class FakeReq:
        user = type('U', (), {'pk': None, 'username': 'test_probe', 'is_authenticated': False})()
        META = {'HTTP_X_FORWARDED_FOR': '127.0.0.1', 'HTTP_USER_AGENT': 'probe/1.0'}
        @property
        def data(self):
            return {}
    row = write_operation_log(
        FakeReq(),
        action='create',
        target_type='user',
        target_repr='probe-target',
        after={'probe': True},
    )
    print(f'  write_operation_log 返回: {row}')
    if row and row.pk:
        print(f'  写入成功 id={row.pk}, operator_username={row.operator_username}')
        # 立刻清掉测试数据，避免污染
        row.delete()
        print(f'  测试数据已清理')
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f'  [FAIL] {type(e).__name__}: {e}')

print()
print('=' * 60)
print('检查完成')
print('=' * 60)