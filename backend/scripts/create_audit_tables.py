"""创建超管审计两张表：admin_login_log / admin_operation_log（幂等）。

说明：这两张表在 Django model 里是 managed=False，不参与 migration，
      需手动建表。SQL 与 database-changes/admin_login_log.md、admin_operation_log.md 保持一致。

运行：python scripts/create_audit_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

import pymysql  # noqa: E402
from django.conf import settings  # noqa: E402

_db = settings.DATABASES['default']
conn = pymysql.connect(
    host=_db['HOST'], user=_db['USER'], password=_db['PASSWORD'],
    port=int(_db['PORT']), database=_db['NAME'], charset='utf8mb4',
    autocommit=True,
)
cur = conn.cursor()

SQL_LOGIN = """
CREATE TABLE IF NOT EXISTS `admin_login_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `user_id` BIGINT NULL COMMENT '登录用户 ID（NULL 表示失败且无法定位）',
    `username` VARCHAR(128) NOT NULL COMMENT '登录用户名（快照）',
    `login_type` VARCHAR(16) NOT NULL COMMENT '登录方式：jwt / sso / sso_mock',
    `success` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否成功：1=成功 / 0=失败',
    `fail_reason` VARCHAR(256) NOT NULL DEFAULT '' COMMENT '失败原因',
    `ip_address` VARCHAR(64) NULL COMMENT '客户端 IP',
    `user_agent` VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'User-Agent',
    `created_at` DATETIME(6) NOT NULL COMMENT '登录时间',
    INDEX `idx_loginlog_user_created` (`user_id`, `created_at`),
    INDEX `idx_loginlog_success_created` (`success`, `created_at`),
    INDEX `idx_loginlog_created` (`created_at`),
    INDEX `idx_loginlog_type_created` (`login_type`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='管理后台登录行为审计日志（由 Django model managed=False 远程手动建表）';
"""

SQL_OPERATION = """
CREATE TABLE IF NOT EXISTS `admin_operation_log` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    `operator_id` BIGINT NULL COMMENT '操作人 ID（NULL 表示匿名）',
    `operator_username` VARCHAR(128) NOT NULL COMMENT '操作人（快照）',
    `action` VARCHAR(32) NOT NULL COMMENT '动作：create/update/delete/reset_password/batch_delete/batch_reset_password',
    `target_type` VARCHAR(32) NOT NULL COMMENT '对象类型：user/resource/category/department',
    `target_id` BIGINT NULL COMMENT '对象 ID（NULL 表示批量）',
    `target_repr` VARCHAR(256) NOT NULL DEFAULT '' COMMENT '对象摘要',
    `after` JSON NULL COMMENT '操作后数据快照',
    `ip_address` VARCHAR(64) NULL COMMENT '客户端 IP',
    `user_agent` VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'User-Agent',
    `created_at` DATETIME(6) NOT NULL COMMENT '操作时间',
    INDEX `idx_oplog_operator_created` (`operator_id`, `created_at`),
    INDEX `idx_oplog_target` (`target_type`, `target_id`),
    INDEX `idx_oplog_action_created` (`action`, `created_at`),
    INDEX `idx_oplog_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='管理后台超管 CRUD 操作审计日志（由 Django model managed=False 远程手动建表）';
"""

cur.execute(SQL_LOGIN)
print('admin_login_log 创建成功')
cur.execute(SQL_OPERATION)
print('admin_operation_log 创建成功')

cur.execute('SHOW TABLES LIKE %s', ('admin_%',))
for r in cur.fetchall():
    print('  -', r[0])

cur.close()
conn.close()
