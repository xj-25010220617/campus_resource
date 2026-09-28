"""2026-09-28 业务规则变更：将「已通过」和「不可用」标记为可进入正式知识库的状态，并移除人工确认审核机制。

本次变更包含两部分：

1. **放行白名单变更**：`unavailable`（审核不可用）现在允许进入正式知识库。
   按新规则，"检测链没能给出结论"不再需要人工复核，直接入库 ——
   因此 `STORAGE_ALLOWED_MODERATION_STATES` 变为 `{approved, unavailable}`。
   `rejected` / `pending` / `scanning` / `error` 仍然一律禁止入库。

2. **人工确认放行机制整体下线**：`confirmed_unchecked` 状态、其三个审计字段
   （`confirmed_by` / `confirmed_at` / `confirmation_reason`）、对应的
   Admin action 与 management command 全部移除。既然 `unavailable` 已自动
   入库，这条人工路径没有存在的意义（详见本轮实施报告）。

迁移内容全部由模型 diff 推导，逐条列出。**不包含任何把禁止状态变成放行状态的
路径**：唯一的 RunPython 只做 `confirmed_unchecked → approved` 的归一。

注意 RunPython 不导入 `apps.knowledge.moderation`：迁移必须对**已冻结的**
历史取值自洽，不能因为将来常量再变而改变本迁移的含义。
"""

from django.conf import settings
from django.db import migrations, models

# 变更前的状态取值（含 confirmed_unchecked），仅用于 AlterField 的历史形态记录。
_OLD_STATE_CHOICES = [
    ('pending', '待审核'),
    ('scanning', '审核中'),
    ('approved', '已通过'),
    ('rejected', '已拒绝'),
    ('unavailable', '审核不可用'),
    ('confirmed_unchecked', '人工确认放行'),
    ('error', '审核异常'),
]

# 变更后的状态取值：confirmed_unchecked 已下线。
_NEW_STATE_CHOICES = [
    ('pending', '待审核'),
    ('scanning', '审核中'),
    ('approved', '已通过'),
    ('rejected', '已拒绝'),
    ('unavailable', '审核不可用'),
    ('error', '审核异常'),
]


def _confirmed_unchecked_to_approved(apps, schema_editor):
    """把历史 `confirmed_unchecked` 归一到 `approved`。

    两者在旧规则下同属放行白名单（人工确认放行 = 显式允许入库），映射到
    `approved` 不改变任何一份文档的可见性。若保留原值，这些文档在新的白名单
    `{approved, unavailable}` 下会变成"不可入库"，等于把已经发布的内容静默撤下。

    反向迁移是 noop：无法从 `approved` 反推出哪些当初是人工确认的，
    编造一个反向映射只会伪造审计事实。
    """
    ContentModeration = apps.get_model('knowledge', 'ContentModeration')
    ContentModeration.objects.filter(state='confirmed_unchecked').update(state='approved')


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('knowledge', '0010_uploadintent_chunkeduploadsession_intent_and_more'),
    ]

    operations = [
        # 1) 先归一数据，再收紧 choices —— 顺序固定，避免出现"取值已不在
        #    choices 里"的中间态。
        migrations.RunPython(
            _confirmed_unchecked_to_approved, migrations.RunPython.noop,
        ),
        # 2) 状态取值下线 confirmed_unchecked（choices 变更，无 DDL 语义差异）
        migrations.AlterField(
            model_name='contentmoderation',
            name='state',
            field=models.CharField(
                choices=_NEW_STATE_CHOICES, db_index=True, default='pending',
                max_length=24, verbose_name='审核状态',
            ),
        ),
        migrations.AlterField(
            model_name='moderationattempt',
            name='state_at_start',
            field=models.CharField(
                choices=_NEW_STATE_CHOICES, default='pending',
                max_length=24, verbose_name='开始时的审核状态',
            ),
        ),
        # 3) 人工确认审计三件套下线：唯一写入方 confirmation.py 已删除，
        #    留着就是三个永远为空的死字段。
        migrations.RemoveField(
            model_name='contentmoderation', name='confirmed_by',
        ),
        migrations.RemoveField(
            model_name='contentmoderation', name='confirmed_at',
        ),
        migrations.RemoveField(
            model_name='contentmoderation', name='confirmation_reason',
        ),
        # 4) 表注释同步（旧注释写着 approved/confirmed_unchecked）
        migrations.AlterModelTableComment(
            name='contentmoderation',
            table_comment=(
                '内容安全审核当前状态表(1:1 knowledge_doc;'
                'state 白名单 approved/unavailable 才可放行;'
                'storage_state=promoted 表示对象已在 knowledge/ 正式命名空间)'
            ),
        ),
    ]
