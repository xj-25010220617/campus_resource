"""
爬取成都东软学院官网并索引入知识库。

用法：
    python manage.py crawl_website                    # 默认爬 50 页
    python manage.py crawl_website --pages 200         # 爬 200 页
    python manage.py crawl_website --uploader 1        # 指定上传者用户 ID
    python manage.py crawl_website --college 1         # 指定归属学院 ID（默认 None=校级）
    python manage.py crawl_website --download-files    # 同时下载文档类附件（默认取设置）
    python manage.py crawl_website --no-download-files # 本次不下载附件
    python manage.py crawl_website --file-types pdf,docx  # 附件扩展名白名单覆盖
    python manage.py crawl_website --max-files 20      # 本次最多下载附件数
    python manage.py crawl_website --dry-run           # 仅发现并列出附件，不下载不入库

支持同时配置定时任务：
    - Linux (crontab):  0 4 * * * cd /path && python manage.py crawl_website --pages 100
    - Windows (计划任务): python manage.py crawl_website --pages 100
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '爬取成都东软学院官网内容（网页正文 + 文档类附件）并索引入知识库'

    def add_arguments(self, parser):
        parser.add_argument('--pages', type=int, default=50, help='爬取页数上限（默认 50）')
        parser.add_argument('--uploader', type=int, default=1, help='上传者用户 ID（默认 1=super_admin）')
        parser.add_argument('--college', type=int, default=None, help='内容归属学院 ID（默认 None=校级公开）')
        parser.add_argument('--download-files', action='store_true', default=None,
                            help='本次爬取同时下载官网文档类附件（默认取设置 CRAWLER_DOWNLOAD_FILES）')
        parser.add_argument('--no-download-files', action='store_true', default=None,
                            help='本次爬取不下载附件（优先级高于 --download-files）')
        parser.add_argument('--file-types', type=str, default=None,
                            help='附件扩展名白名单覆盖，逗号分隔（如 pdf,docx）')
        parser.add_argument('--max-files', type=int, default=None,
                            help='本次最多下载附件数（默认取设置 CRAWLER_MAX_FILES）')
        parser.add_argument('--dry-run', action='store_true',
                            help='仅发现并列出附件清单，不下载、不入库')

    def handle(self, *args, **options):
        max_pages = options['pages']
        uploader_id = options['uploader']
        college_id = options['college']

        # --no-download-files 优先于 --download-files；都未指定时取设置默认值（None）
        if options['no_download_files']:
            download_files = False
        elif options['download_files']:
            download_files = True
        else:
            download_files = None

        # dry-run：只跑发现阶段，spider 不写库不下载
        if options['dry_run']:
            from utils.crawler import FILE_TYPES, MAX_FILES_PER_RUN, NeusoftSpider

            spider = NeusoftSpider(download_files=True, max_files=MAX_FILES_PER_RUN)
            self.stdout.write(f'dry-run 开始发现附件: pages={max_pages}')
            spider.start_crawl(max_pages=max_pages)
            self.stdout.write(f'发现 {len(spider.file_links)} 个附件（未下载/未入库）:')
            for f in spider.file_links[:50]:
                self.stdout.write(f'  - {f["url"]}  |  {f["text"]}')
            if len(spider.file_links) > 50:
                self.stdout.write(f'  ...（其余 {len(spider.file_links) - 50} 个省略）')
            self.stdout.write(f'当前白名单扩展名: {", ".join(sorted(FILE_TYPES))}')
            return

        from utils.crawler import crawl_and_index

        self.stdout.write(
            f'开始爬取: pages={max_pages}, uploader_id={uploader_id}, '
            f'college_id={college_id}, download_files={download_files}'
        )
        count = crawl_and_index(
            max_pages=max_pages,
            uploader_id=uploader_id,
            college_id=college_id,
            download_files=download_files,
            file_types=options['file_types'],
            max_files=options['max_files'],
        )
        self.stdout.write(self.style.SUCCESS(f'爬取完成: 共保存 {count} 篇文档（含附件）'))
