"""
JS 渲染页面抓取工具

对 JS 动态渲染的页面提供 Playwright 降级抓取。
优先使用 requests（静态 HTML），内容不足时自动尝试 JS 渲染。

Playwright 为可选依赖，仅当 PLAYWRIGHT_ENABLED=True 时启用。
安装: pip install playwright && playwright install chromium
"""
import logging
import re
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# 配置
PLAYWRIGHT_ENABLED = getattr(settings, 'PLAYWRIGHT_ENABLED', False)
PLAYWRIGHT_TIMEOUT = getattr(settings, 'PLAYWRIGHT_TIMEOUT', 15000)     # ms
PLAYWRIGHT_HEADLESS = getattr(settings, 'PLAYWRIGHT_HEADLESS', True)

# JS 渲染页面正文最低字符数阈值（低于此值判定为 JS 渲染）
MIN_BODY_TEXT_CHARS = 200


def is_js_rendered(html: str) -> bool:
    """
    判断页面是否为 JS 渲染（静态 HTML 中无实质内容）。

    通过提取 <body> 内的纯文本长度判断：
    - 正文 < MIN_BODY_TEXT_CHARS → 大概率是 JS 动态渲染
    - SPA 标志：body 内只有根容器（如 <div id="app">, <div id="root">）

    Args:
        html: 原始 HTML 字符串

    Returns:
        True 表示需要 JS 渲染才能获取内容
    """
    if not html:
        return True

    # 检查常见 SPA 根容器标志
    spa_markers = [
        r'<div[^>]*\bid\s*=\s*["\'](?:app|root|__next|__nuxt)["\']',
        r'<div[^>]*\bid\s*=\s*["\'](?:react-root|vue-app|main-app)["\']',
    ]
    for marker in spa_markers:
        if re.search(marker, html, re.IGNORECASE):
            return True

    # 提取 body 纯文本
    body_match = re.search(
        r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE,
    )
    if not body_match:
        # 无 body 标签，可能是不完整的响应
        return True

    # 去除所有 HTML 标签和脚本样式
    body_text = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', body_match.group(1),
                       flags=re.DOTALL | re.IGNORECASE)
    body_text = re.sub(r'<[^>]+>', '', body_text).strip()

    return len(body_text) < MIN_BODY_TEXT_CHARS


def render_page(url: str, timeout: int = None) -> Optional[str]:
    """
    使用 Playwright 渲染 JS 页面并返回完整 HTML。

    仅在 PLAYWRIGHT_ENABLED=True 且 Playwright 已安装时生效。
    失败返回 None，调用方应回退到已有的降级逻辑。

    Args:
        url: 目标页面 URL
        timeout: 渲染超时（毫秒），默认使用配置值

    Returns:
        渲染后的 HTML 字符串，失败返回 None
    """
    if not PLAYWRIGHT_ENABLED:
        logger.debug('Playwright 未启用，跳过 JS 渲染')
        return None

    timeout = timeout or PLAYWRIGHT_TIMEOUT

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug(
            'Playwright 未安装，无法渲染 JS 页面。'
            '安装: pip install playwright && playwright install chromium'
        )
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=PLAYWRIGHT_HEADLESS)
            page = browser.new_page()
            page.goto(url, wait_until='networkidle', timeout=timeout)
            html = page.content()
            browser.close()
            logger.info(
                'Playwright 渲染成功: %s (%d 字符)', url, len(html),
            )
            return html
    except Exception as e:
        logger.warning('Playwright 渲染失败: %s - %s', url, e)
        return None
