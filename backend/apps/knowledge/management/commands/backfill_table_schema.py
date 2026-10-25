"""
回填历史表格文档的 table_schema 元数据。

遍历已索引的 xls/csv 文档，重新从 OSS 拉取并提取表格结构落库；
不重建向量、不影响现有索引，可反复执行（幂等）。

用法：
    python manage.py backfill_table_schema [--dry-run]
"""
import logging

from django.core.management.base import BaseCommand

from apps.knowledge.extractor import extract_text_from_oss_detailed
from apps.knowledge.models import KnowledgeDoc

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '回填已索引 xls/csv 文档的 table_schema（表格问答引擎元数据）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只统计待回填文档数量，不实际执行',
        )

    def handle(self, *args, **options):
        qs = KnowledgeDoc.objects.filter(
            status='indexed',
            file_type='xls',
        )
        total = qs.count()
        self.stdout.write(f'待检查文档: {total}')
        if options['dry_run']:
            return

        updated = skipped = failed = 0
        for doc in qs.iterator():
            try:
                parsed = extract_text_from_oss_detailed(
                    doc.oss_key, doc.file_type, doc.file_name,
                )
            except Exception as exc:
                failed += 1
                logger.warning('doc_id=%d 回填异常: %s', doc.id, exc)
                continue

            if parsed.table_schema:
                doc.table_schema = parsed.table_schema
                doc.save(update_fields=['table_schema', 'updated_at'])
                updated += 1
                self.stdout.write(
                    f'  doc_id={doc.id} {doc.title[:30]} → '
                    f'{len(parsed.table_schema.get("sheets", []))} sheet'
                )
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'完成: 回填 {updated}，非结构化跳过 {skipped}，失败 {failed}'
        ))
