"""Test fixed start_crawl"""
import logging
logging.basicConfig(level=logging.INFO)

from utils.crawler import NeusoftSpider
spider = NeusoftSpider()
pages = spider.start_crawl(max_pages=10)
print(f'== 总计: {len(pages)} 页 ==')
for p in pages:
    print(f'  [{p["title"][:30]}] | {len(p["content"])}字')
