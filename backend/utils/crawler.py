"""
成都东软学院官网爬虫 (Crawler)

使用 requests + BeautifulSoup 广度优先爬取官网信息，
提取正文后保存为 KnowledgeDoc（source_type='website'），
再由 Celery 异步任务完成分块 → Embedding → Milvus 索引。

网站结构（基于 www.neusoft.edu.cn）：
  列表页: /html/{section_id}/
  详情页: /html/{section_id}/{date}/content-{number}.html
"""
import hashlib
import logging
import re
import time
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 配置读取（优先 Django settings，否则使用默认值）
# ------------------------------------------------------------------
def _get_config(key: str, default):
    """尝试从 Django settings 读取配置。"""
    try:
        from django.conf import settings
        return getattr(settings, key, default)
    except Exception:
        return default


NEUSOFT_BASE_URL = _get_config("NEUSOFT_WEBSITE_URL", "https://www.nsu.edu.cn")
CRAWL_DELAY = _get_config("CRAWLER_CRAWL_DELAY", 0.5)

# 重要栏目路径片段（新站 www.nsu.edu.cn 结构）
IMPORTANT_SECTION_PATHS = [
    "/HTML/service/",   # 通知公告 / 服务指南
    "/HTML/news/",      # 新闻动态
    "/HTML/media/",     # 媒体关注
    "/HTML/notice/",    # 通知公告
]

# 不需要爬取的路径片段（附件、外链等）
EXCLUDE_PATH_PATTERNS = [
    r"\.(pdf|zip|rar|doc|docx|xls|xlsx|ppt|pptx|jpg|jpeg|png|gif|mp4|avi)$",
    r"/sites/html/xxgk/",  # 信息公开（格式特殊）
]

# 请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 爬取控制
REQUEST_TIMEOUT = 15


# ------------------------------------------------------------------
# URL 路径 → 分类 ID 映射（用于自动分类官网内容）
# ------------------------------------------------------------------
# 根据爬取的栏目路径，自动匹配到系统中已有的二级分类
_SECTION_CATEGORY_MAP = {
    "/html/984/": 41,   # 通知公告 → 行政公文>通知公告
    "/html/977/": 23,   # 校园时讯 → 学科建设>学校要闻
    "/html/978/": 27,   # 媒体聚焦 → 人事档案>媒体报道
    "/html/985/": 41,   # 活动预告 → 行政公文>通知公告
    "/html/1008/": 44,  # 学校概况 → 行政公文>会议纪要（综合信息）
    "/html/1078/": 50,  # 人才培养 → 教学建设>教学质量
    "/html/1011/": 50,  # 办学特色 → 教学建设>教学质量
    "/html/1052/": 76,  # 招生就业 → 学生工作>招生就业
    "/html/1075/": 58,  # 一流专业 → 专业建设>一流专业建设
    "/html/1076/": 78,  # 创业学院 → 学生工作>学生活动
    "/html/1037/": 78,  # 校园活动 → 学生工作>学生活动
    "/html/1038/": 25,  # 社团建设 → 专业建设>社团活动
    "/html/951/": 44,   # 发展历程 → 行政公文>会议纪要
    "/html/1066/": 77,  # 学生意见反馈 → 学生工作>奖助管理
    "/html/840/": 24,   # DNUI影像 → 专业建设>图文东软
}


def _get_category_id_for_url(url: str) -> int:
    """根据 URL 自动匹配分类 ID。"""
    for path, cat_id in _SECTION_CATEGORY_MAP.items():
        if path in url:
            return cat_id
    return 41  # 默认：通知公告
# ------------------------------------------------------------------
def _normalize_url(href: str, base: str) -> Optional[str]:
    """将相对路径转为绝对 URL，并过滤非法链接。"""
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    full = urljoin(base, href)
    # 只保留同域链接（或子域）
    parsed = urlparse(full)
    if not parsed.netloc.endswith("neusoft.edu.cn"):
        return None
    # 去掉 fragment
    clean = full.split("#")[0]
    return clean


def _is_excluded(url: str) -> bool:
    """判断 URL 是否应排除（附件、特殊格式页面）。"""
    for pattern in EXCLUDE_PATH_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def _is_detail_page(url: str) -> bool:
    """判断是否为文章详情页（而非列表页/首页）。"""
    return bool(re.search(r"/content-\d+\.html", url))


def _is_section_page(url: str) -> bool:
    """判断是否为栏目列表页。"""
    return bool(re.search(r"/html/\d+/$", url))


def _is_important_section(url: str) -> bool:
    """判断是否在重要栏目列表中。"""
    for path in IMPORTANT_SECTION_PATHS:
        if path in url:
            return True
    return False


# ------------------------------------------------------------------
# 内容提取
# ------------------------------------------------------------------
def extract_text_from_html(html: str, url: str = "") -> dict:
    """从 HTML 中提取文章标题、发布时间、正文。

    Returns:
        {"title": str, "publish_date": str, "content": str}
    """
    soup = BeautifulSoup(html, "lxml")
    result = {"title": "", "publish_date": "", "content": ""}

    # 1. 标题
    title_tag = soup.find("h1") or soup.find("h2") or soup.find("title")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)
    # 用 <title> 兜底
    if not result["title"] and soup.title:
        result["title"] = soup.title.get_text(strip=True)

    # 2. 发布时间（常见 class 模式）
    time_patterns = [
        {"attrs": {"class": re.compile(r"time|date|publish", re.I)}},
        {"name": "span", "class": re.compile(r"time|date", re.I)},
        {"name": "em", "class": re.compile(r"time|date", re.I)},
    ]
    for pat in time_patterns:
        tag = soup.find(**pat) if "name" in pat else soup.find(attrs=pat["attrs"])
        if tag:
            result["publish_date"] = tag.get_text(strip=True)
            break
    # 正则兜底（常见时间格式）
    if not result["publish_date"]:
        date_match = re.search(
            r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)",
            html[:2000],
        )
        if date_match:
            result["publish_date"] = date_match.group(1)

    # 3. 正文 - 尝试多种选择器
    content_selectors = [
        {"class_": "content"},
        {"class_": "article"},
        {"class_": "detail"},
        {"class_": "main"},
        {"class_": re.compile(r"article|content|detail", re.I)},
        {"id": re.compile(r"article|content|detail", re.I)},
    ]
    content_div = None
    for sel in content_selectors:
        content_div = soup.find("div", **sel)
        if content_div:
            break

    if content_div:
        # 移除脚本/样式/隐藏元素
        for tag in content_div.find_all(["script", "style", "nav", "footer"]):
            tag.decompose()
        result["content"] = content_div.get_text(separator="\n", strip=True)
    else:
        # 兜底：提取 body 中所有段落
        body = soup.find("body")
        if body:
            for tag in body.find_all(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            paragraphs = body.find_all("p")
            texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
            result["content"] = "\n\n".join(texts)

    return result


# ------------------------------------------------------------------
# 爬虫核心
# ------------------------------------------------------------------
class NeusoftSpider:
    """成都东软官网爬虫 - BFS 广度优先"""

    def __init__(self, base_url: str = NEUSOFT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.seen_urls: Set[str] = set()
        self.seen_hashes: Set[str] = set()  # 内容去重

    def _fetch(self, url: str) -> Optional[str]:
        """请求 URL 返回 HTML，失败返回 None。JS 页面自动尝试 Playwright 渲染。"""
        if url in self.seen_urls:
            return None
        self.seen_urls.add(url)
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            time.sleep(CRAWL_DELAY)
            html = resp.text

            # 检测 JS 渲染页面，尝试 Playwright 兜底
            from utils.js_renderer import is_js_rendered, render_page
            if is_js_rendered(html):
                logger.info('爬虫检测到 JS 渲染页面，尝试 Playwright: %s', url)
                rendered = render_page(url)
                if rendered:
                    return rendered

            return html
        except requests.RequestException as e:
            logger.warning(f"请求失败: {url} - {e}")
            return None

    def _extract_links(self, html: str, page_url: str) -> List[str]:
        """从页面提取所有同域链接。"""
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            full = _normalize_url(a["href"], page_url)
            if full and not _is_excluded(full):
                links.append(full)
        return links

    def crawl_page(self, url: str) -> Optional[dict]:
        """抓取单个页面并提取内容。

        Returns:
            {"title": str, "publish_date": str, "content": str, "url": str}
        """
        html = self._fetch(url)
        if not html:
            return None
        extracted = extract_text_from_html(html, url)
        if not extracted["content"] or len(extracted["content"]) < 50:
            logger.debug(f"内容过短，跳过: {url}")
            return None
        extracted["url"] = url
        return extracted

    def crawl_section(self, section_url: str, max_pages: int = 50) -> List[dict]:
        """爬取单个栏目下的所有文章。

        从列表页开始，提取文章详情页链接 → 逐个抓取详情。
        """
        results = []
        to_visit = [section_url]
        visited_list_pages = set()

        while to_visit and len(results) < max_pages:
            url = to_visit.pop(0)
            if url in visited_list_pages:
                continue
            visited_list_pages.add(url)

            html = self._fetch(url)
            if not html:
                continue

            # 提取本页所有链接
            links = self._extract_links(html, url)

            for link in links:
                if _is_detail_page(link) and link not in self.seen_urls:
                    page_data = self.crawl_page(link)
                    if page_data:
                        # 内容去重
                        content_hash = hashlib.md5(
                            page_data["content"][:500].encode()
                        ).hexdigest()
                        if content_hash not in self.seen_hashes:
                            self.seen_hashes.add(content_hash)
                            results.append(page_data)
                            logger.info(
                                f"已爬取: {page_data['title'][:40]} "
                                f"({len(results)}/{max_pages})"
                            )
                            if len(results) >= max_pages:
                                return results

                elif _is_section_page(link):
                    # 列表页翻页
                    if link not in visited_list_pages and link not in to_visit:
                        to_visit.append(link)

        return results

    def start_crawl(self, max_pages: int = 100) -> List[dict]:
        """全站爬取入口。

        1. 先爬首页
        2. 提取所有重要栏目链接
        3. 逐个栏目爬取
        """
        logger.info(f"开始全站爬取: max_pages={max_pages}")
        all_results = []

        # 1. 获取首页 HTML（一次性：用于内容提取 + 栏目链接提取）
        html = self._fetch(self.base_url)
        if not html:
            logger.warning("无法获取首页 HTML，终止爬取")
            return []

        # 解析首页内容
        extracted = extract_text_from_html(html, self.base_url)
        if extracted["content"] and len(extracted["content"]) >= 50:
            extracted["url"] = self.base_url
            all_results.append(extracted)

        # 2. 从首页 HTML 中提取重要栏目链接
        links = self._extract_links(html, self.base_url)
        section_urls = set()
        for link in links:
            if _is_important_section(link):
                section_urls.add(link)

        logger.info(f"发现 {len(section_urls)} 个重要栏目")

        # 3. 爬取每个栏目
        remaining = max_pages - len(all_results)
        for section_url in sorted(section_urls):
            if remaining <= 0:
                break
            section_results = self.crawl_section(
                section_url,
                max_pages=min(20, remaining),
            )
            all_results.extend(section_results)
            remaining = max_pages - len(all_results)

        logger.info(f"全站爬取完成: 共 {len(all_results)} 页")
        return all_results


# ------------------------------------------------------------------
# 与 KnowledgeDoc 集成
# ------------------------------------------------------------------
def save_page_as_knowledge_doc(
    page_data: dict,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
) -> Optional[int]:
    """将爬取的页面保存为 KnowledgeDoc 并触发异步索引。

    自动根据 URL 路径匹配分类（通知公告→行政公文/通知公告，校园时讯→学科建设/学校要闻等），
    文本内容上传到 OSS 后走标准索引流程。

    Args:
        page_data: crawl_page() 返回的字典
        uploader_id: 上传者用户 ID（默认 admin）
        college_id: 所属学院 ID
        discipline_id: 学科方向 ID（可选，默认从上传者所在学院推断）

    Returns:
        KnowledgeDoc.id，失败返回 None
    """
    try:
        import django
        import os
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()
    except Exception:
        pass

    from apps.knowledge.models import KnowledgeDoc

    title = page_data.get("title", "") or "无标题"
    content = page_data.get("content", "")
    url = page_data.get("url", "")

    if not content or len(content) < 50:
        logger.warning(f"内容过短，跳过保存: {title}")
        return None

    # 检查是否已存在相同 URL
    if KnowledgeDoc.objects.filter(source_url=url).exists():
        logger.info(f"URL 已存在，跳过: {url}")
        return None

    # 生成 MD5 去重
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    if KnowledgeDoc.objects.filter(md5_hash=content_hash).exists():
        logger.info(f"内容已存在（MD5 重复），跳过: {title}")
        return None

    # 获取上传者
    from django.contrib.auth import get_user_model
    User = get_user_model()
    try:
        uploader = User.objects.get(id=uploader_id)
    except User.DoesNotExist:
        uploader = User.objects.filter(is_superuser=True).first()
        if not uploader:
            logger.error("无法找到上传者用户")
            return None

    # 如果未指定 discipline_id，尝试从上传者所在学院推断第一个学科方向
    if discipline_id is None and college_id:
        from apps.classify.models import Discipline
        first = Discipline.objects.filter(
            knowledge_docs__college_id=college_id
        ).order_by("id").first()
        if first:
            discipline_id = first.id
            logger.debug(f"自动推断 discipline_id={discipline_id}（college_id={college_id}）")

    # 上传内容到 OSS（索引任务需要从 OSS 读取文本）
    import uuid
    from datetime import datetime
    from utils.oss import oss_client

    now = datetime.now()
    safe_filename = f"crawl_{uuid.uuid4().hex[:12]}.txt"
    oss_key = f"knowledge/website/0/{now:%Y-%m}/{safe_filename}"

    upload_success = oss_client.upload_text(content, oss_key)
    if not upload_success:
        logger.error(f"OSS 上传失败，跳过保存: {title}")
        return None

    oss_url = (
        f"https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}"
        if oss_client.bucket_name and oss_client.endpoint
        else ""
    )

    # 创建 KnowledgeDoc（使用系统的分类体系）
    doc = KnowledgeDoc.objects.create(
        title=title[:512],
        description=title[:200],
        source_type="website",
        source_url=url,
        file_type="txt",
        file_size=len(content.encode("utf-8")),
        md5_hash=content_hash,
        oss_key=oss_key,
        oss_url=oss_url,
        visibility="public",
        scope="school",
        uploader=uploader,
        college_id=college_id,
        discipline_id=discipline_id,
        # category_id 不传（category 表已移除）
        status="uploaded",
    )

    logger.info(
        f"已创建 KnowledgeDoc: id={doc.id}, title={title[:40]}, "
        f"college_id={college_id}"
    )

    # 触发异步索引
    try:
        from utils.celery_dispatcher import safe_delay
        from apps.knowledge.tasks import index_document_task
        safe_delay(index_document_task, doc.id)
    except Exception as e:
        logger.warning(f"触发索引任务失败: {e}")

    return doc.id


def crawl_and_index(
    max_pages: int = 50,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
) -> int:
    """全站爬取并自动索引到知识库。

    Args:
        max_pages: 本次爬取最大页数
        uploader_id: 上传者用户 ID
        college_id: 所属学院 ID
        discipline_id: 学科方向 ID（可选，默认自动推断）

    Returns:
        成功保存的文档数
    """
    spider = NeusoftSpider()
    pages = spider.start_crawl(max_pages=max_pages)

    saved = 0
    for page in pages:
        doc_id = save_page_as_knowledge_doc(
            page,
            uploader_id=uploader_id,
            college_id=college_id,
            discipline_id=discipline_id,
        )
        if doc_id:
            saved += 1

    logger.info(f"爬取并索引完成: 共爬取 {len(pages)} 页，保存 {saved} 篇文档")
    return saved
