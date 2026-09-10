"""
存量 FAQ 分类回填命令

用法:
    python manage.py backfill_faq_categories             # 回填所有 category 为空的条目
    python manage.py backfill_faq_categories --dry-run   # 只预览判定结果，不写库

调用 LLM 对 question+answer 判定分类（限定 faq_category 表中的分类名），
匹配成功则写回 item.category。分类表为空时请先执行: python manage.py seed_faq --category-only
"""
from django.core.management.base import BaseCommand

from apps.faq.models import FAQItem
from apps.faq.utils import classify_faq_category, get_faq_category_names, resolve_category_id


class Command(BaseCommand):
    help = '为 category 为空的存量 FAQ 条目回填分类（调用 LLM 判定）'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='仅输出判定结果，不写库')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run')

        category_names = get_faq_category_names()
        if not category_names:
            self.stdout.write(self.style.ERROR(
                'faq_category 表为空，请先执行: python manage.py seed_faq --category-only'
            ))
            return

        items = FAQItem.objects.filter(category__isnull=True).order_by('id')
        total = items.count()
        self.stdout.write(f'待回填条目: {total} 条，候选分类: {"、".join(category_names)}')
        if not total:
            self.stdout.write(self.style.SUCCESS('[OK] 无待回填条目'))
            return

        matched = 0
        for item in items:
            name = classify_faq_category(item.question, item.answer)
            category_id = resolve_category_id(name) if name else None
            self.stdout.write(f'  #{item.id} [{name or "未匹配"}] {item.question[:40]}')
            if dry_run:
                continue
            if category_id:
                item.category_id = category_id
                item.save(update_fields=['category'])
                matched += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'[OK] 预览完成，共 {total} 条（未写库）'))
        else:
            self.stdout.write(self.style.SUCCESS(f'[OK] 回填完成: {matched}/{total} 条已归类'))
