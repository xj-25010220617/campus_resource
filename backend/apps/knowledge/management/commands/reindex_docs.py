"""知识库重索引（P1-2 Phase 1-2 / 审计报告 P-1、P-9）。

用法：
    # 只看统计，不产生任何写入（推荐先跑这个）
    python manage.py reindex_docs --dry-run

    # 预检通过后，重建所有**通过审核门**的文档
    python manage.py reindex_docs

    # 连已经 indexed 的也重建（仍然逐条过审核门）
    python manage.py reindex_docs --force --limit 50

本命令是唯一受支持的运维重索引入口；`scripts/reindex_all.bat` 与
`scripts/index_all_docs.py` / `index_quick.py` 都收敛到同一条路径，
避免"哪份脚本才是权威"的问题。

安全边界（全部由 `apps.knowledge.reindex_tool` 保证，本命令只做参数解析）：
- `--dry-run` 纯只读：不写库、不碰 OSS/Milvus、不删分块
- 默认模式在**存在任一不可索引文档**时直接退出（fail-closed），不做任何写入
- `--force` **不绕过审核门**：只允许重做已 indexed 的文档
- 任何模式下都不做"批量删除分块 / 批量重置 status / 清空向量库"
"""
from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.reindex_tool import ReindexBlocked, run_reindex


class Command(BaseCommand):
    help = '重索引知识库文档（默认只处理通过审核门的文档，支持 --dry-run / --force）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只输出统计，不产生任何写入（不删分块、不改状态、不碰 OSS/Milvus）',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='同时重建已经 indexed 的文档（不解除审核门，不处理未审核文档）',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='最多处理多少份文档（默认全部）',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('知识库重索引'))
        try:
            result = run_reindex(
                dry_run=options['dry_run'],
                force=options['force'],
                limit=options['limit'],
                out=self.stdout.write,
            )
        except ReindexBlocked as e:
            # fail-closed：预检没过就不动数据。退出码非 0，便于 CI / 运维脚本感知。
            raise CommandError(str(e))

        if result['dry_run']:
            self.stdout.write(self.style.WARNING(
                'dry-run 结束：未产生任何写入。'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'完成：处理 {result["processed"]} 份，'
                f'跳过不可索引 {result["skipped"]} 份。'
            ))
