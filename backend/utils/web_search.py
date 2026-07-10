"""
联网搜索工具（基于百度搜索）

通过百度搜索引擎获取实时信息，解决 JS 网站无法直接抓取的问题。
不存储、不索引，每次按需搜索。
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


def search(query: str, max_results: int = 5) -> List[dict]:
    """搜索百度，返回结果列表。"""
    try:
        from baidusearch.baidusearch import search as _baidu_search
        raw = list(_baidu_search(query, num_results=max_results))
    except Exception as e:
        logger.warning(f'百度搜索失败: {e}')
        return []

    results = []
    for item in raw:
        results.append({
            'title': item.get('title', ''),
            'url': item.get('url', ''),
            'snippet': item.get('abstract', ''),
        })
    return results


def search_school(query: str, max_results: int = 5) -> List[dict]:
    """针对成都东软学院搜索。"""
    return search(f'成都东软学院 {query}', max_results=max_results)
