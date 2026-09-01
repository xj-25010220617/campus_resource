"""
实时网页抓取工具（轻量，不存储，不分块，不索引）

用于智能体在对话中按需获取网页内容并分析。
区别于爬虫（crawler.py）：不存储到知识库，不索引到向量库，用完即弃。
"""
import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# SSRF 防护：禁止访问的 IP 段（回环/私网/链路本地/保留/组播/广播/未指定/云元数据）
_BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('100.64.0.0/10'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.0.0.0/24'),
    ipaddress.ip_network('192.0.2.0/24'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('198.18.0.0/15'),
    ipaddress.ip_network('198.51.100.0/24'),
    ipaddress.ip_network('203.0.113.0/24'),
    ipaddress.ip_network('224.0.0.0/4'),
    ipaddress.ip_network('240.0.0.0/4'),
    ipaddress.ip_network('255.255.255.255/32'),
]
_BLOCKED_IP_NETWORKS += [
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('::/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
    ipaddress.ip_network('ff00::/8'),
]


def _is_blocked_ip(ip_str: str) -> bool:
    """判断单个 IP 是否落入禁止访问段。"""
    try:
        ip = ipaddress.ip_address(ip_str.split('%')[0])
    except ValueError:
        return True  # 无法解析的 IP 一律拦截
    return any(ip in net for net in _BLOCKED_IP_NETWORKS)


def validate_target_url(url: str) -> tuple[bool, str]:
    """SSRF 防护：校验待抓取 URL 是否允许访问。

    Returns:
        (是否允许, 拒绝原因或空串)
    """
    try:
        parts = urlsplit(url)
    except ValueError as e:
        return False, f'URL 格式非法: {e}'

    if parts.scheme not in ('http', 'https'):
        return False, f'仅支持 http/https 协议，收到: {parts.scheme or "(空)"}'
    if not parts.hostname:
        return False, 'URL 缺少主机名'
    if parts.username or parts.password:
        return False, 'URL 不允许包含用户凭据'

    host = parts.hostname

    # 主机名是 IP 字面量 → 直接校验
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_blocked_ip(host):
            return False, f'禁止访问内网/保留地址: {host}'
    else:
        # 域名 → 解析全部 IP 并逐一校验（缓解 DNS rebinding）
        try:
            addrinfos = socket.getaddrinfo(host, None)
        except OSError as e:
            return False, f'域名解析失败: {e}'
        resolved = {ai[4][0] for ai in addrinfos}
        if not resolved:
            return False, f'域名无法解析: {host}'
        for ip_str in sorted(resolved):
            if _is_blocked_ip(ip_str):
                return False, f'域名 {host} 解析到禁止访问的地址: {ip_str}'

    return True, ''


def _assert_safe_url(url: str) -> None:
    """校验 URL 安全，不通过则抛 ValueError（调用方应捕获）。"""
    ok, reason = validate_target_url(url)
    if not ok:
        raise ValueError(reason)

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
    # SSRF 防护：禁止内网/保留地址
    ok, reason = validate_target_url(url)
    if not ok:
        logger.warning(f'SSRF 拦截: {url} - {reason}')
        return None

    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
            # 不允许自动跟随重定向，重定向目标也需逐个校验（由调用方用 session 控制）
            allow_redirects=False,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or 'utf-8'
    except Exception as e:
        logger.warning(f'网页抓取失败: {url} - {e}')
        return None

    # 手动跟随最多 3 次重定向，每次均做 SSRF 校验
    for _ in range(3):
        location = resp.headers.get('Location')
        if not location or resp.status_code not in (301, 302, 303, 307, 308):
            break
        next_url = requests.compat.urljoin(url, location)
        ok, reason = validate_target_url(next_url)
        if not ok:
            logger.warning(f'SSRF 拦截（重定向）: {next_url} - {reason}')
            return None
        try:
            resp = requests.get(
                next_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=False,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or 'utf-8'
        except Exception as e:
            logger.warning(f'网页抓取失败（重定向）: {next_url} - {e}')
            return None
        url = next_url

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
