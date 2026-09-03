from django.core.management.base import BaseCommand, CommandError

from apps.faq.tasks import generate_faq_from_website


class Command(BaseCommand):
    help = '抓取官网内容并生成待审核 FAQ 草稿'

    def add_arguments(self, parser):
        parser.add_argument('--pages', type=int, default=None, help='最多抓取页数')
        parser.add_argument('--max-docs', type=int, default=None, help='最多处理文档数')

    def handle(self, *args, **options):
        pages = options['pages']
        max_docs = options['max_docs']
        if pages is not None and pages <= 0:
            raise CommandError('--pages 必须大于 0')
        if max_docs is not None and max_docs <= 0:
            raise CommandError('--max-docs 必须大于 0')

        result = generate_faq_from_website(
            max_pages=pages,
            max_docs=max_docs,
        )
        if result is None:
            self.stdout.write(self.style.WARNING('官网 FAQ 生成未执行（已有任务运行中或日志已结束）。'))
            return
        self.stdout.write(self.style.SUCCESS(f'官网 FAQ 生成完成，日志 ID: {result}'))
