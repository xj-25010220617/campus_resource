"""人工确认放行（`confirmed_unchecked`）的命令行入口。

用法：
    # 先看看哪些文档卡在 unavailable
    python manage.py confirm_moderation --list

    # 放行一份文档（理由必填）
    python manage.py confirm_moderation \\
        --doc-id 123 --actor zhangwei --reason "ClamAV 故障期间人工复核，已下载检查无毒"

本命令**不自己写库**，只做参数校验后调用
`apps.knowledge.confirmation.confirm_unchecked_document()`：
权限、状态、隔离区对象存在性、审计三件套全部由 service 负责，命令行与
Django Admin action 共用同一条路径，不会出现"两个入口两套判断"。

安全边界（与 service 一致）：
- 只有管理员角色（apps.admin_panel.permissions.ADMIN_ROLES）可以执行
- 只允许 `unavailable` → `confirmed_unchecked`，`rejected` 永远不能翻案
- 只接受真实存在于 `quarantine/` 的对象
- 确认后仍然走 promotion → knowledge/ → index，**不绕过提升**
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '人工确认放行卡在 unavailable 的文档（写入 confirmed_unchecked）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--doc-id', type=int, default=None,
            help='要放行的 KnowledgeDoc 主键',
        )
        parser.add_argument(
            '--actor', type=str, default=None,
            help='执行确认的管理员用户名（必须有管理员角色，会写入 confirmed_by）',
        )
        parser.add_argument(
            '--reason', type=str, default=None,
            help='确认理由（必填，会写入 confirmation_reason）',
        )
        parser.add_argument(
            '--list', action='store_true',
            help='只列出当前可人工确认放行的文档，不做任何修改',
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        from apps.knowledge.confirmation import (
            CONFIRMABLE_MODERATION_STATES,
            ConfirmationError,
            can_confirm,
            confirm_unchecked_document,
        )
        from apps.knowledge.models import ContentModeration, KnowledgeDoc

        if options['list']:
            return self._list_confirmable(ContentModeration, CONFIRMABLE_MODERATION_STATES)

        doc_id = options['doc_id']
        actor_name = options['actor']
        reason = options['reason']

        missing = [
            name for name, value in
            (('--doc-id', doc_id), ('--actor', actor_name), ('--reason', reason))
            if not value
        ]
        if missing:
            raise CommandError(
                '缺少必填参数：%s（可先用 --list 查看候选文档）' % ', '.join(missing)
            )

        User = get_user_model()
        try:
            actor = User.objects.get(username=actor_name)
        except User.DoesNotExist:
            raise CommandError('执行人不存在：%s' % actor_name)

        # 权限在此处提前给出一条友好提示；真正的判定仍在 service 内（唯一入口）
        if not can_confirm(actor):
            raise CommandError(
                '用户 %s 的角色为 %s，不是管理员，不能执行人工确认'
                % (actor_name, getattr(actor, 'role', 'unknown'))
            )

        try:
            doc = KnowledgeDoc.objects.get(pk=doc_id)
        except KnowledgeDoc.DoesNotExist:
            raise CommandError('文档不存在：%s' % doc_id)

        try:
            moderation = confirm_unchecked_document(
                doc, actor=actor, reason=reason, worker_id='cli',
            )
        except ConfirmationError as e:
            raise CommandError('确认失败（%s）：%s' % (e.reason, e.detail))

        self.stdout.write(self.style.SUCCESS(
            '已确认放行 doc=%s moderation=%s state=%s\n'
            '后续仍将走 promotion → knowledge/ → 索引，请用 --list 或后台确认进展。'
            % (doc.pk, moderation.pk, moderation.state)
        ))

    # ------------------------------------------------------------------
    def _list_confirmable(self, ContentModeration, confirmable_states):
        """列出候选：仅 unavailable 记录，且带隔离区 Key。只读。"""
        qs = (
            ContentModeration.objects
            .filter(state__in=sorted(confirmable_states))
            .exclude(quarantine_key='')
            .select_related('doc')
            .order_by('updated_at')
        )
        rows = list(qs)
        if not rows:
            self.stdout.write('当前没有可人工确认放行的文档。')
            return

        self.stdout.write('可人工确认放行的文档（%d 份）：' % len(rows))
        self.stdout.write('%-8s %-8s %-22s %-16s %s' % (
            'cm_id', 'doc_id', 'state', 'storage_state', 'quarantine_key',
        ))
        for m in rows:
            self.stdout.write('%-8s %-8s %-22s %-16s %s' % (
                m.pk, m.doc_id, m.state, m.storage_state, m.quarantine_key,
            ))
        self.stdout.write('')
        self.stdout.write(
            '放行示例：python manage.py confirm_moderation '
            '--doc-id %s --actor <管理员用户名> --reason "<理由>"' % rows[0].doc_id
        )
