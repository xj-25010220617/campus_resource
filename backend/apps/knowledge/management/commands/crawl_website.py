"""
爬取成都东软学院官网并索引入知识库。

用法：
    python manage.py crawl_website                    # 默认爬 50 页
    python manage.py crawl_website --pages 200         # 爬 200 页
    python manage.py crawl_website --uploader 1        # 指定上传者用户 ID
    python manage.py crawl_website --college 1         # 指定归属学院 ID（默认 None=校级）

支持同时配置定时任务：
    - Linux (crontab):  0 4 * * * cd /path && python manage.py crawl_website --pages 100
    - Windows (计划任务): python manage.py crawl_website --pages 100
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '爬取成都东软学院官网内容并索引入知识库'

    def add_arguments(self, parser):
        parser.add_argument('--pages', type=int, default=50, help='爬取页数上限（默认 50）')
        parser.add_argument('--uploader', type=int, default=1, help='上传者用户 ID（默认 1=super_admin）')
        parser.add_argument('--college', type=int, default=None, help='内容归属学院 ID（默认 None=校级公开）')

    def handle(self, *args, **options):
        from utils.crawler import crawl_and_index

        max_pages = options['pages']
        uploader_id = options['uploader']
        college_id = options['college']

        self.stdout.write(f'开始爬取: pages={max_pages}, uploader_id={uploader_id}, college_id={college_id}')
        count = crawl_and_index(
            max_pages=max_pages,
            uploader_id=uploader_id,
            college_id=college_id,
        )
        self.stdout.write(self.style.SUCCESS(f'爬取完成: 保存 {count} 篇文档'))
