"""
实时网页抓取工具（轻量，不存储，不分块，不索引）

用于智能体在对话中按需获取网页内容并分析。
区别于爬虫（crawler.py）：不存储到知识库，不索引到向量库，用完即弃。
"""
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

REQUEST_TIMEOUT = 15
MAX_CONTENT_CHARS = 6000  # 单页最多取前 6000 字


def fetch_page(url: str) -> Optional[str]:
    """抓取单页并提取正文（纯文本），供 LLM 实时分析。

    Args:
        url: 目标网页 URL

    Returns:
        提取的正文文本，失败返回 None
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or 'utf-8'
    except Exception as e:
        logger.warning(f'网页抓取失败: {url} - {e}')
        return None

    html = resp.text

    # 检测 JS 渲染页面，尝试 Playwright 兜底
    from utils.js_renderer import is_js_rendered, render_page
    if is_js_rendered(html):
        logger.info('检测到 JS 渲染页面，尝试 Playwright: %s', url)
        rendered = render_page(url)
        if rendered:
            html = rendered

    soup = BeautifulSoup(html, 'lxml')

    # 提取标题
    title = ''
    h1 = soup.find('h1')
    if h1:
        title = h1.get_text(strip=True)
    elif soup.title:
        title = soup.title.get_text(strip=True)

    # 提取正文：尝试常见的内容容器
    content_selectors = [
        {'class_': 'content'},
        {'class_': 'article'},
        {'class_': 'detail'},
        {'class_': 'main'},
        {'class_': re.compile(r'article|content|detail|main', re.I)},
        {'id': re.compile(r'article|content|detail|main', re.I)},
    ]
    content_div = None
    for sel in content_selectors:
        content_div = soup.find('div', **sel)
        if content_div and len(content_div.get_text(strip=True)) > 100:
            break

    if content_div:
        for tag in content_div.find_all(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        body = content_div.get_text(separator='\n', strip=True)
    else:
        # 兜底：取 body 所有段落
        body_tag = soup.find('body')
        if body_tag:
            for tag in body_tag.find_all(['script', 'style', 'nav', 'footer', 'header']):
                tag.decompose()
            paras = body_tag.find_all('p')
            texts = [p.get_text(strip=True) for p in paras if p.get_text(strip=True)]
            body = '\n\n'.join(texts)
        else:
            body = ''

    # 拼接结果
    result = ''
    if title:
        result += f'标题：{title}\n\n'
    result += body[:MAX_CONTENT_CHARS]

    if len(body) > MAX_CONTENT_CHARS:
        result += '\n\n...（内容过长，仅展示前 {MAX_CONTENT_CHARS} 字）'

    return result if len(result) > 50 else None
