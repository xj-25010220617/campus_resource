# -*- coding: utf-8 -*-
"""E2E 测试种子数据：账号 / 知识文档 / FAQ / 各类日志 / 反馈

运行方式（在 backend 目录下）：
    python manage.py shell -c "exec(open('seed_e2e.py', encoding='utf-8').read())"

幂等：文档/FAQ/日志类数据先清空再重建；账号使用 get_or_create，不改动既有数据。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from datetime import timedelta
from django.db import transaction, connection
from django.utils import timezone

from apps.accounts.models import College, User
from apps.classify.models import Department, Discipline
from apps.faq.models import FAQCategory, FAQItem
from apps.knowledge.models import KnowledgeDoc
from apps.resources.models import UploadLog
from apps.chat.models import QueryLog, SensitiveLog

now = timezone.now()
PWD = 'Admin@123456'


# ══════════════════════════════════════════════════════════════════
# 一、账号（设定密码 → UI 账号密码登录可测）
# ══════════════════════════════════════════════════════════════════
def ensure_user(username, role, college=None, dept=None, first='', last='', email=None):
    user, created = User.objects.get_or_create(
        username=username,
        defaults=dict(role=role, first_name=first, last_name=last,
                      email=email or f'{username}@nsu.edu.cn'),
    )
    user.role = role
    if college is not None:
        user.college = College.objects.get(id=college)
    if dept is not None:
        user.department = Department.objects.get(id=dept)
    user.set_password(PWD)
    user.save()
    return user


@transaction.atomic
def seed_users():
    # 平台级
    ensure_user('sso_super_admin', 'super_admin', last='超级管理员')
    ensure_user('sso_admin', 'admin', last='管理员')
    # 学院级
    cadmin = ensure_user('cadmin', 'college_admin', college=1, last='学院管理员')
    # 部门级
    dadmin = ensure_user('dadmin', 'dept_admin', dept=13, last='部门管理员')
    # 普通用户
    student = ensure_user('student', 'user', college=1, last='学生')
    teacher = ensure_user('teacher', 'user', college=1, last='教师')
    ensure_user('sso_college_admin', 'college_admin', college=1, last='学院管理员')
    ensure_user('sso_student', 'user', college=1, last='学生')
    ensure_user('sso_teacher', 'user', college=1, last='教师')
    # SSO mock 用户也设密码（保持 UI 登录可用）
    for u in User.objects.filter(username__in=[
        'sso_super_admin', 'sso_admin', 'sso_college_admin', 'sso_student', 'sso_teacher']):
        u.set_password(PWD)
        u.save()
    print(f'[users] {User.objects.count()} 个账号（登录密码均为 {PWD}）')
    return cadmin, dadmin, student, teacher


# ══════════════════════════════════════════════════════════════════
# 二、知识文档
# ══════════════════════════════════════════════════════════════════
def seed_docs(admin_u, cadmin_u, dadmin_u, student_u, teacher_u):
    KnowledgeDoc.objects.all().delete()
    c1 = College.objects.get(id=1)
    d13 = Department.objects.get(id=13)
    d1 = Department.objects.get(id=1)
    disc_sw = Discipline.objects.filter(id=1).first()

    docs = [
        # (title, college, dept, uploader, file_type, status, visibility, size, days_ago, desc)
        ('《软件工程导论》教学大纲', None, None, admin_u, 'md', 'indexed', 'public', 256000, 60, '计算机学院牵头编写的全校公开教学大纲'),
        ('学校网络服务使用指南', None, None, admin_u, 'txt', 'indexed', 'public', 48000, 45, '覆盖校园网、VPN、无线网络常见问题'),
        ('图书馆电子资源使用手册', None, None, teacher_u, 'pdf', 'indexed', 'public', 3200000, 30, '图书馆数据库与电子期刊使用说明'),
        ('计算机组成原理实验指导', c1, None, cadmin_u, 'doc', 'indexed', 'public', 1024000, 20, '计算机学院内部实验课程指导书'),
        ('软件工程课程期末试卷A卷', c1, None, cadmin_u, 'xls', 'indexed', 'private', 89000, 15, '仅供本院教师查阅'),
        ('学生宿舍网络故障报修流程', None, d13, dadmin_u, 'md', 'indexed', 'public', 15000, 10, '后勤与教学管理部门联合发布'),
        ('我的毕业设计选题报告', c1, None, User.objects.get(username='sso_student'), 'doc', 'indexed', 'public', 220000, 5, 'sso_student 个人上传文档'),
        ('课堂笔记-操作系统原理', c1, None, User.objects.get(username='sso_student'), 'md', 'parsing', 'public', 12000, 2, '解析中状态示例'),
        ('我的个人学习笔记', c1, None, student_u, 'md', 'indexed', 'public', 18000, 3, 'UI 用户 student 上传'),
        ('保密涉密管理办法(草案)', None, None, admin_u, 'doc', 'uploaded', 'private', 66000, 1, '未发布私密文档'),
        ('旧版教师评教表', c1, None, teacher_u, 'xls', 'failed', 'public', 33000, 40, '解析失败示例'),
    ]
    for title, college, dept, uploader, ftype, status, vis, size, days, desc in docs:
        KnowledgeDoc.objects.create(
            title=title, description=desc, source_type='upload',
            file_name=f'{title}.{ftype}', file_type=ftype,
            visibility=vis, file_size=size, page_count=max(1, size // 40000),
            college=college, department=dept, uploader=uploader,
            status=status, chunk_count=status == 'indexed' and 8 or 0,
            discipline=disc_sw,
            created_at=now - timedelta(days=days),
        )
    print(f'[docs] 知识文档 {KnowledgeDoc.objects.count()} 篇')


# ══════════════════════════════════════════════════════════════════
# 三、FAQ 分类 + 条目
# ══════════════════════════════════════════════════════════════════
def seed_faq():
    FAQItem.objects.all().delete()
    FAQCategory.objects.all().delete()
    cats = {
        '账号与登录': ['登录', '账号'],
        '教务教学': ['选课', '成绩'],
        '校园网络': ['网络', 'VPN'],
        '生活服务': ['食堂', '宿舍'],
    }
    objs = {}
    for name, tags in cats.items():
        c, _ = FAQCategory.objects.update_or_create(name=name, defaults={'sort_order': list(cats).index(name)})
        objs[name] = c

    published = [
        ('如何重置账号密码？', '在登录页点击「忘记密码」，输入学号或工号与绑定的手机号，系统将发送重置链接至邮箱。', ['账号', '密码'], '账号与登录'),
        ('登录时提示账号不存在怎么办？', '请确认使用学号/工号登录，且账号已经由教务处统一开通。新生需等待入学信息同步。', ['账号', '登录'], '账号与登录'),
        ('如何查看本学期课程成绩？', '登录教务系统（http://jw.nsu.edu.cn）后，在「成绩查询」中选择对应学期即可。', ['成绩'], '教务教学'),
        ('选课系统开放时间是什么？', '每学期第 2 周开放选课，具体时间以教务处公告为准，建议提前登录系统。', ['选课'], '教务教学'),
        ('校园网如何连接？', '连接 NSU-WLAN 后使用学号与统一身份认证密码登录，即可访问校园网络。', ['网络'], '校园网络'),
        ('校外如何访问图书馆资源？', '通过 VPN 客户端（webvpn.nsu.edu.cn）接入校园网后即可访问全部电子资源。', ['VPN', '网络'], '校园网络'),
        ('宿舍网络故障如何报修？', '拨打网络中心热线 028-64888000，或在企业微信中搜索「网络报修」提交工单。', ['网络', '宿舍'], '校园网络'),
        ('食堂饭卡如何充值？', '通过企业微信「一卡通」入口充值，或前往各食堂自助充值机办理。', ['食堂'], '生活服务'),
        ('宿舍报修流程是什么？', '在企业微信「后勤服务」中选择「宿舍报修」，填写楼栋房间号与故障描述即可。', ['宿舍'], '生活服务'),
        ('图书馆借阅规则？', '学生每人可借 10 册图书，借期 30 天，可续借一次。逾期按每册每天 0.1 元计费。', ['图书馆'], '生活服务'),
    ]
    for q, a, tags, cat in published:
        FAQItem.objects.create(question=q, answer=a, tags=tags, category=objs[cat], status='published', frequency=3.0)
    FAQItem.objects.create(question='内部测试草稿问题', answer='草稿内容，普通用户不可见。', tags=['内部'], status='draft')
    FAQItem.objects.create(question='已驳回的旧问题', answer='该条目已被驳回。', tags=['旧'], status='rejected')
    print(f'[faq] 分类 {FAQCategory.objects.count()} 个 / 条目 {FAQItem.objects.count()} 条（published={FAQItem.objects.filter(status="published").count()}）')


# ══════════════════════════════════════════════════════════════════
# 四、日志（dashboard 数据源）
# ══════════════════════════════════════════════════════════════════
def ensure_log_tables():
    """admin_panel 两张表是 managed=False（生产手工建表），本地需自行建表。"""
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_login_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NULL,
                username VARCHAR(128) NOT NULL,
                login_type VARCHAR(16) NOT NULL,
                success TINYINT(1) NOT NULL DEFAULT 0,
                fail_reason VARCHAR(256) NOT NULL DEFAULT '',
                ip_address VARCHAR(45) NULL,
                user_agent VARCHAR(512) NOT NULL DEFAULT '',
                created_at DATETIME(6) NOT NULL,
                KEY idx_created (created_at),
                KEY idx_success (success),
                KEY idx_type (login_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admin_operation_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                operator_id BIGINT NULL,
                operator_username VARCHAR(128) NOT NULL,
                action VARCHAR(32) NOT NULL,
                target_type VARCHAR(32) NOT NULL,
                target_id BIGINT NULL,
                target_repr VARCHAR(256) NOT NULL DEFAULT '',
                after JSON NULL,
                ip_address VARCHAR(45) NULL,
                user_agent VARCHAR(512) NOT NULL DEFAULT '',
                created_at DATETIME(6) NOT NULL,
                KEY idx_created (created_at),
                KEY idx_action (action)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
    print('[logs] admin_login_log / admin_operation_log 表已就绪')


def seed_logs(admin_u, cadmin_u, student_u):
    from apps.admin_panel.models import LoginLog, OperationLog
    UploadLog.objects.all().delete()
    QueryLog.objects.all().delete()
    SensitiveLog.objects.all().delete()
    LoginLog.objects.all().delete()
    OperationLog.objects.all().delete()

    # 上传日志
    uploads = [
        ('《软件工程导论》教学大纲.md', 'md', 'success', 256000, 1, 30),
        ('网络服务指南.txt', 'txt', 'success', 48000, None, 25),
        ('图书馆电子资源手册.pdf', 'pdf', 'success', 3200000, 1, 20),
        ('计算机组成原理实验指导.doc', 'doc', 'success', 1024000, 1, 18),
        ('课程期末试卷A卷.xls', 'xls', 'success', 89000, 2, 12),
        ('学生宿舍报修流程.md', 'md', 'success', 15000, None, 8),
        ('毕业设计选题报告.doc', 'doc', 'success', 220000, 1, 5),
        ('旧版教师评教表.xls', 'xls', 'failed', 33000, 2, 40),
        ('重复上传示例.md', 'md', 'pending', 5000, 1, 1),
    ]
    for name, ftype, status, size, cid, days in uploads:
        UploadLog.objects.create(
            user=cadmin_u if cid else admin_u,
            file_name=name, file_size=size, file_type=ftype,
            college=College.objects.get(id=cid) if cid else College.objects.get(id=1),
            department=Department.objects.get(id=13) if not cid else None,
            status=status, error_message='' if status != 'failed' else '文件编码无法解析',
            ip_address='127.0.0.1', user_agent='Playwright-E2E',
            created_at=now - timedelta(days=days),
            completed_at=(now - timedelta(days=days)) if status == 'success' else None,
        )

    # 查询日志
    for i in range(10):
        QueryLog.objects.create(
            user=student_u if i % 2 else cadmin_u,
            question=f'测试问题 {i + 1}：如何访问校园网络',
            hit_count=i % 4, response_ms=800 + i * 120,
            user_feedback=1 if i % 3 == 0 else (0 if i % 3 == 1 else -1),
            created_at=now - timedelta(days=i // 3, hours=i),
        )

    # 敏感日志
    for i in range(3):
        SensitiveLog.objects.create(
            user=student_u, question=f'包含敏感词的提问 {i + 1}',
            matched_rules=[{'rule': '测试规则'}], matched_texts=[f'敏感片段{i + 1}'],
            refuse_message='抱歉，该问题涉及敏感内容，无法回答。',
            created_at=now - timedelta(days=i, hours=2),
        )

    # 登录日志
    for i in range(10):
        LoginLog.objects.create(
            user=admin_u if i % 2 else student_u, username='test_user',
            login_type='sso_mock' if i % 3 else 'jwt', success=i % 4 != 0,
            fail_reason='' if i % 4 else '密码错误',
            ip_address='127.0.0.1', user_agent='Playwright-E2E',
            created_at=now - timedelta(days=i // 2, hours=i),
        )

    # 操作日志
    for action, tt in [('create', 'user'), ('update', 'user'), ('delete', 'resource'),
                       ('reset_password', 'user'), ('batch_delete', 'user')]:
        OperationLog.objects.create(
            operator=admin_u, operator_username='admin', action=action,
            target_type=tt, target_id=1, target_repr='测试对象',
            after={'ok': True}, ip_address='127.0.0.1', user_agent='Playwright-E2E',
            created_at=now - timedelta(hours=len(action)),
        )
    print(f'[logs] upload={UploadLog.objects.count()} query={QueryLog.objects.count()} '
          f'sensitive={SensitiveLog.objects.count()} login={LoginLog.objects.count()} '
          f'operation={OperationLog.objects.count()}')


# 注意：python manage.py shell -c 的 __name__ 不是 '__main__'，故不加该守卫
ensure_log_tables()
cadmin, dadmin, student, teacher = seed_users()
seed_docs(admin_u=User.objects.get(username='sso_admin'), cadmin_u=cadmin,
          dadmin_u=dadmin, student_u=student, teacher_u=teacher)
seed_faq()
seed_logs(admin_u=User.objects.get(username='sso_admin'), cadmin_u=cadmin, student_u=student)
print('═' * 40)
print('E2E 种子数据完成')
