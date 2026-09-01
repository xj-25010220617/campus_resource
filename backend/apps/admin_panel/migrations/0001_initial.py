"""创建 admin_panel 两张审计表（admin_login_log / admin_operation_log）。

背景：这两张表的 model 均为 `managed = False`（apps/admin_panel/models.py），
表结构一直靠 database-changes/admin_login_log.md、admin_operation_log.md 里的
手写 SQL 在远程库手动创建，`python manage.py migrate` 不会建。因此在新库/新服务器上
必然缺表，登录审计（write_login_log）报 1146 table doesn't exist。

本迁移用幂等 RunSQL（CREATE TABLE IF NOT EXISTS）把两张表纳入迁移体系：
- 任何环境跑 `python manage.py migrate` 都会自动建表；表已存在则跳过，安全可重复执行。
- 表结构需与 apps/admin_panel/models.py 保持一致（含索引，供 dashboard 按区间聚合）。
- reverse 迁移删除这两张表（谨慎：会丢失审计数据）。
"""

from django.db import migrations

_ADMIN_LOGIN_LOG_SQL = """
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
COMMENT='管理后台登录行为审计日志'
"""

_ADMIN_OPERATION_LOG_SQL = """
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
COMMENT='管理后台超管操作审计日志'
"""


class Migration(migrations.Migration):

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=[_ADMIN_LOGIN_LOG_SQL, _ADMIN_OPERATION_LOG_SQL],
            reverse_sql=[
                'DROP TABLE IF EXISTS `admin_operation_log`;',
                'DROP TABLE IF EXISTS `admin_login_log`;',
            ],
        ),
    ]
