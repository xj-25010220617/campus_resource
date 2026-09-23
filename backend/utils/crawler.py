"""
成都东软学院官网爬虫 (Crawler)

使用 requests + BeautifulSoup 广度优先爬取官网信息，
提取正文后保存为 KnowledgeDoc（source_type='website'），
再由 Celery 异步任务完成分块 → Embedding → Milvus 索引。

同时支持附件下载：发现页面中的文档类附件（pdf/doc/xls 等，见 CRAWLER_FILE_TYPES），
流式下载后经 OSS 直传，创建 KnowledgeDoc(source_type='website') 走与手动上传一致的索引链路。

网站结构（基于 www.nsu.edu.cn 成都东软学院官网）：
  栏目列表页: /HTML/{section}/                          （section ∈ news/service/teach/intro/image/dept/recruit）
  列表翻页页: /HTML/{section}/list_{N}.html
  文章详情页: /HTML/{section}/article_{N}.html           （新闻类含日期子目录 /HTML/news/{year}/{month}/article_{N}.html）
"""
import hashlib
import logging
import re
import time
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, unquote

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

# 重要栏目路径片段（www.nsu.edu.cn 成都东软学院官网结构，匹配不区分大小写）
IMPORTANT_SECTION_PATHS = [
    "/HTML/news/",     # 新闻动态
    "/HTML/service/",  # 服务指南
    "/HTML/teach/",    # 教学科研
    "/HTML/intro/",    # 学校概况
    "/HTML/image/",    # 影像东软
    "/HTML/dept/",     # 院系设置
    "/HTML/recruit/",  # 招生就业
]

# 不需要爬取的路径片段（附件、外链等）
EXCLUDE_PATH_PATTERNS = [
    r"\.(pdf|zip|rar|doc|docx|xls|xlsx|ppt|pptx|jpg|jpeg|png|gif|mp4|avi)$",
    r"/sites/html/xxgk/",  # 信息公开（正文格式特殊，不提取文本；附件发现见 download_files 分支）
]

# 附件/媒体类 URL（任何场景都不作为页面爬取；文档类由 collect_file_links 单独发现下载）
MEDIA_FILE_RE = re.compile(
    r"\.(pdf|zip|rar|doc|docx|xls|xlsx|ppt|pptx|jpg|jpeg|png|gif|mp4|avi)$",
    re.IGNORECASE,
)

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
# 附件下载配置（仅下载可解析的文档类，图片/音视频/压缩包不下载）
# ------------------------------------------------------------------
DOWNLOAD_FILES = bool(_get_config("CRAWLER_DOWNLOAD_FILES", True))
MAX_FILE_SIZE = int(_get_config("CRAWLER_MAX_FILE_SIZE_MB", 50)) * 1024 * 1024
MAX_FILES_PER_RUN = int(_get_config("CRAWLER_MAX_FILES", 100))
# (连接超时, 读取超时)：附件可能较大，读取超时单独放宽
FILE_TIMEOUT = (REQUEST_TIMEOUT, int(_get_config("CRAWLER_FILE_TIMEOUT", 60)))

_cfg_types = str(_get_config("CRAWLER_FILE_TYPES", "pdf,doc,docx,xls,xlsx,ppt,pptx,csv,txt,md"))
FILE_TYPES = {t.strip().lower() for t in _cfg_types.split(",") if t.strip()}
# 附件扩展名匹配（仅匹配 URL path，不匹配 query）
_FILE_EXT_RE = re.compile(r"\.(%s)$" % "|".join(sorted(FILE_TYPES)), re.IGNORECASE)

_cfg_hints = str(_get_config("CRAWLER_FILE_URL_HINTS", "download"))
FILE_URL_HINTS = tuple(h.strip().lower() for h in _cfg_hints.split(",") if h.strip())

# 明显是网页后缀的 URL 不作为候选附件（注意不含 .jsp：download.jsp 是常见下载入口）
PAGE_EXT_RE = re.compile(r"\.(html?|shtml|aspx|php|do|action)$", re.IGNORECASE)

# Content-Type → 扩展名（仅文档类；application/octet-stream 不映射，交给文件名扩展名判定）
CONTENT_TYPE_EXT_MAP = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/markdown": "md",
}


# ------------------------------------------------------------------
# 附件 URL / 响应头解析
# ------------------------------------------------------------------
def is_file_url(url: str) -> bool:
    """判断 URL 是否为文档类附件（按 unquote 后的 path 扩展名判定，query 不参与）。"""
    path = unquote(urlparse(url).path)
    return bool(_FILE_EXT_RE.search(path.lower()))


def is_file_candidate_url(url: str) -> bool:
    """判断无扩展名 URL 是否为候选附件（download.jsp 类下载入口）。

    path 含 FILE_URL_HINTS 关键词（如 download）且不是明显网页后缀才视为候选，
    是否真为附件由下载后的 Content-Type 二次确认。
    """
    path = unquote(urlparse(url).path).lower()
    if PAGE_EXT_RE.search(path):
        return False
    return any(hint in path for hint in FILE_URL_HINTS)


def guess_ext_from_content_type(content_type: str) -> str:
    """从响应 Content-Type 推断文档扩展名，无法识别返回空串。"""
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXT_MAP.get(ct, "")


def parse_content_disposition(header: str) -> str:
    """解析 Content-Disposition 头中的文件名，解析失败返回空串。

    支持 RFC5987（filename*=UTF-8''...）与普通 filename="..." 两种形式；
    服务端 latin-1 编码导致的中文乱码做一次 GBK 修复尝试。
    """
    if not header:
        return ""
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', header)
    if not m:
        m = re.search(r"filename\s*=\s*([^;]+)", header)
        if not m:
            return ""
    name = m.group(1).strip().strip('"')
    try:
        # requests 按 latin-1 解码头，中文文件名需还原原始字节后按 GBK 解码
        fixed = name.encode("latin-1").decode("gbk")
        name = fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass  # 非 GBK 乱码，保留原值
    return name


def sanitize_file_name(name: str, ext: str) -> str:
    """清洗文件名：去除路径分隔符/控制字符与原扩展名，重新拼接白名单扩展名，适配 file_name 字段。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name or "").strip()
    if "." in name:
        name = name.rsplit(".", 1)[0]
    stem = name[:200] or "crawl_file"
    return f"{stem}.{ext}"


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
    if not parsed.netloc.endswith("nsu.edu.cn"):
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
    """判断是否为文章详情页（而非列表页/首页）。

    nsu.edu.cn 详情页：/HTML/{section}/article_{N}.html
    （新闻类含日期子目录，如 /HTML/news/2026/09/article_4924.html）
    """
    return bool(re.search(r"/article_\d+\.html", url, re.IGNORECASE))


def _is_section_page(url: str) -> bool:
    """判断是否为栏目列表页（/HTML/{section}/）。"""
    return bool(re.search(r"/HTML/[a-z]+/$", url, re.IGNORECASE))


def _is_page_list(url: str) -> bool:
    """判断是否为列表翻页页（/HTML/{section}/list_N.html）。"""
    return bool(re.search(r"/list_\d+\.html$", url, re.IGNORECASE))


def _is_important_section(url: str) -> bool:
    """判断是否在重要栏目列表中（不区分大小写，兼容新旧站结构）。"""
    lower = url.lower()
    for path in IMPORTANT_SECTION_PATHS:
        if path.lower() in lower:
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

    def __init__(
        self,
        base_url: str = NEUSOFT_BASE_URL,
        download_files: bool = DOWNLOAD_FILES,
        allowed_exts: Optional[Set[str]] = None,
        max_files: int = MAX_FILES_PER_RUN,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.seen_urls: Set[str] = set()
        self.seen_hashes: Set[str] = set()  # 内容去重
        # 附件下载：总开关 / 扩展名白名单 / 单次运行附件上限
        self.download_files = download_files
        self.allowed_exts = set(allowed_exts) if allowed_exts else set(FILE_TYPES)
        self.max_files = max_files
        self.seen_file_urls: Set[str] = set()  # 附件 URL 去重（运行内）
        self.file_links: List[dict] = []       # 发现的附件 {"url","text","source_page"}

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

    def _extract_links(self, html: str, page_url: str, keep_page_excluded: bool = False) -> List[str]:
        """从页面提取所有同域链接。

        Args:
            keep_page_excluded: 为 True 时保留"被文本抽取排除的页面"链接（如信息公开），
                供附件发现复用；附件/媒体类 URL 任何场景下都不保留（由 collect_file_links 负责）。
        """
        soup = BeautifulSoup(html, "lxml")
        links = []
        for a in soup.find_all("a", href=True):
            full = _normalize_url(a["href"], page_url)
            if not full:
                continue
            if MEDIA_FILE_RE.search(full):
                continue
            if _is_excluded(full) and not keep_page_excluded:
                continue
            links.append(full)
        return links

    def collect_file_links(self, html: str, page_url: str) -> List[dict]:
        """从页面发现文档类附件链接（只登记不下载），锚文本作标题提示。

        同一 URL 全局只登记一次；登记数达到 max_files 即停止发现。
        """
        if not self.download_files or len(self.file_links) >= self.max_files:
            return []
        found = []
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            full = _normalize_url(a["href"], page_url)
            if not full or full in self.seen_file_urls:
                continue
            if is_file_url(full):
                # 扩展名白名单在发现阶段即过滤，避免窄白名单时白下载
                path_ext = unquote(urlparse(full).path).rsplit(".", 1)[-1].lower()
                if path_ext not in self.allowed_exts:
                    continue
            elif not is_file_candidate_url(full):
                continue
            self.seen_file_urls.add(full)
            link = {
                "url": full,
                "text": a.get_text(strip=True)[:200],
                "source_page": page_url,
            }
            self.file_links.append(link)
            found.append(link)
            if len(self.file_links) >= self.max_files:
                logger.info(f"附件发现已达上限 {self.max_files}，停止收集")
                break
        if found:
            logger.debug(f"页面 {page_url} 发现 {len(found)} 个附件候选")
        return found

    def crawl_page(self, url: str) -> Optional[dict]:
        """抓取单个页面并提取内容。

        Returns:
            {"title": str, "publish_date": str, "content": str, "url": str}
        """
        html = self._fetch(url)
        if not html:
            return None
        extracted = extract_text_from_html(html, url)
        # 附件发现：放在"内容过短"判断之前，避免正文极短的下载页丢失附件
        if self.download_files:
            self.collect_file_links(html, url)
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
            links = self._extract_links(html, url, keep_page_excluded=self.download_files)

            # 列表页也可能直接挂附件链接
            if self.download_files:
                self.collect_file_links(html, url)

            for link in links:
                if self.download_files and _is_excluded(link):
                    # 文本抽取被排除的页面（如信息公开 xxgk）：仅做附件发现，不提取正文入库
                    excluded_html = self._fetch(link)
                    if excluded_html:
                        self.collect_file_links(excluded_html, link)

                elif _is_detail_page(link) and link not in self.seen_urls:
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

                elif _is_section_page(link) or _is_page_list(link):
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
        links = self._extract_links(html, self.base_url, keep_page_excluded=self.download_files)

        # 首页也可能直接挂附件链接
        if self.download_files:
            self.collect_file_links(html, self.base_url)
        section_urls = set()
        for link in links:
            if _is_important_section(link):
                section_urls.add(link)
            elif self.download_files and _is_excluded(link):
                # 文本抽取排除的特殊页面（如信息公开 xxgk）：仅用于附件发现
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
    # 附件下载（独立于页面抓取的二进制路径，不走 _fetch/Playwright）
    # ------------------------------------------------------------------
    def _resolve_ext_and_name(self, resp, url: str) -> Tuple[str, str]:
        """从响应头/URL 判定附件扩展名与原始文件名。

        优先级：Content-Disposition 文件名 > URL path basename > Content-Type 反查。
        返回 (ext, raw_name)，无法判定白名单内扩展名时 ext 为空串。
        """
        cd_name = parse_content_disposition(resp.headers.get("Content-Disposition", ""))
        path_name = unquote(urlparse(url).path).rsplit("/", 1)[-1]
        raw_name = cd_name or path_name
        ct_ext = guess_ext_from_content_type(resp.headers.get("Content-Type", ""))

        ext = ""
        # 1. 文件名自带的扩展名（Content-Disposition 或 URL）
        for candidate in (cd_name, path_name):
            if "." in candidate:
                cand_ext = candidate.rsplit(".", 1)[-1].strip().lower()
                if cand_ext in self.allowed_exts:
                    ext = cand_ext
                    break
        # 2. Content-Type 反查
        if not ext:
            ext = ct_ext if ct_ext in self.allowed_exts else ""
        return ext, raw_name

    def download_file(self, file_link: dict) -> Optional[dict]:
        """流式下载单个附件，超过大小上限即中止。

        Args:
            file_link: collect_file_links() 登记的 {"url","text","source_page"}

        Returns:
            {"url","file_name","ext","content","size","md5","title_hint","source_page"}，
            失败/超限/非文档类型返回 None
        """
        url = file_link["url"]
        try:
            resp = self.session.get(
                url, timeout=FILE_TIMEOUT, stream=True,
                headers={"Accept": "*/*"},
            )
        except requests.RequestException as e:
            logger.warning(f"附件请求失败: {url} - {e}")
            return None

        try:
            resp.raise_for_status()
            # Content-Type 为网页说明是误伤的页面（如 /download/ 列表页），放弃
            content_type = resp.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() in ("text/html", "application/xhtml+xml"):
                logger.debug(f"候选 URL 实为网页，跳过: {url}")
                return None

            ext, raw_name = self._resolve_ext_and_name(resp, url)
            if not ext:
                logger.debug(f"附件扩展名不在白名单内，跳过: {url} (Content-Type={content_type})")
                return None

            # 大小上限：单扩展名限制（与手动上传一致）与爬虫全局上限取较小者
            limit = MAX_FILE_SIZE
            try:
                from apps.knowledge.serializers import FILE_SIZE_LIMITS
                limit = min(limit, FILE_SIZE_LIMITS.get(ext, MAX_FILE_SIZE))
            except Exception as e:
                # Django 未初始化等场景退化为全局上限
                logger.debug(f"读取 FILE_SIZE_LIMITS 失败，使用全局上限: {e}")
            content_length = resp.headers.get("Content-Length", "")
            try:
                if content_length and int(content_length) > limit:
                    logger.warning(f"附件超过大小上限({limit // 1024 // 1024}MB)，中止: {url}")
                    return None
            except ValueError:
                pass  # Content-Length 非法时依赖逐块累计判断

            data = bytearray()
            for chunk in resp.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > limit:
                    logger.warning(f"附件超过大小上限({limit // 1024 // 1024}MB)，中止: {url}")
                    return None

            if not data:
                logger.warning(f"附件内容为空: {url}")
                return None

            return {
                "url": url,
                "file_name": sanitize_file_name(raw_name, ext),
                "ext": ext,
                "content": bytes(data),
                "size": len(data),
                "md5": hashlib.md5(bytes(data)).hexdigest(),
                "title_hint": file_link.get("text", ""),
                "source_page": file_link.get("source_page", ""),
            }
        except requests.RequestException as e:
            logger.warning(f"附件下载失败: {url} - {e}")
            return None
        finally:
            resp.close()


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


def save_file_as_knowledge_doc(
    file_data: dict,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
) -> Optional[int]:
    """将下载的官网附件保存为 KnowledgeDoc 并触发异步索引。

    与手动上传契约一致：二进制经 OSS 直传，file_name 保留真实扩展名
    （索引任务按扩展名选择解析器），file_type 经 normalize_file_type 归一化。

    Args:
        file_data: download_file() 返回的字典
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
    from apps.knowledge.serializers import normalize_file_type

    url = file_data.get("url", "")
    content = file_data.get("content", b"")
    size = file_data.get("size", 0)
    md5 = file_data.get("md5", "")
    file_name = file_data.get("file_name", "")
    ext = file_data.get("ext", "other")

    if not content or not file_name:
        logger.warning(f"附件数据不完整，跳过保存: {url}")
        return None
    if size > MAX_FILE_SIZE:
        logger.warning(f"附件超过大小上限，跳过保存: {url}")
        return None

    # 去重：同 URL 已入库 / 同内容（MD5）已入库
    if KnowledgeDoc.objects.filter(source_url=url[:1024]).exists():
        logger.info(f"附件 URL 已存在，跳过: {url}")
        return None
    if KnowledgeDoc.objects.filter(md5_hash=md5).exists():
        logger.info(f"附件内容已存在（MD5 重复），跳过: {file_name}")
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

    # 标题：优先锚文本，否则文件名主干（库已迁移 utf8mb4，无需 4 字节清洗）
    title = (file_data.get("title_hint", "") or "").strip()
    if not title:
        title = file_name.rsplit(".", 1)[0]
    title = (title or "官网附件")[:512]

    # 上传二进制到 OSS（索引任务需要从 OSS 读取文件）
    import uuid
    from datetime import datetime
    from utils.oss import guess_content_type, oss_client

    now = datetime.now()
    safe_filename = f"crawl_{uuid.uuid4().hex[:12]}.{ext}"
    oss_key = f"knowledge/website/0/{now:%Y-%m}/{safe_filename}"

    upload_success = oss_client.upload_bytes(
        content, oss_key, content_type=guess_content_type(file_name)
    )
    if not upload_success:
        logger.error(f"OSS 上传失败，跳过保存: {file_name}")
        return None

    oss_url = (
        f"https://{oss_client.bucket_name}.{oss_client.endpoint}/{oss_key}"
        if oss_client.bucket_name and oss_client.endpoint
        else ""
    )

    source_page = file_data.get("source_page", "")
    description = f"官网附件：{file_name}（来源页面: {source_page}）" if source_page else f"官网附件：{file_name}"

    doc = KnowledgeDoc.objects.create(
        title=title,
        description=description[:200],
        source_type="website",
        source_url=url[:1024],
        file_name=file_name[:256],
        file_type=normalize_file_type(ext),
        file_size=size,
        md5_hash=md5,
        oss_key=oss_key,
        oss_url=oss_url,
        visibility="public",
        scope="school",
        uploader=uploader,
        college_id=college_id,
        discipline_id=discipline_id,
        status="uploaded",
    )

    logger.info(
        f"已创建附件 KnowledgeDoc: id={doc.id}, file_name={file_name[:40]}, "
        f"size={size}, college_id={college_id}"
    )

    # 触发异步索引
    try:
        from utils.celery_dispatcher import safe_delay
        from apps.knowledge.tasks import index_document_task
        safe_delay(index_document_task, doc.id)
    except Exception as e:
        logger.warning(f"触发索引任务失败: {e}")

    return doc.id


def download_and_save_files(
    spider: "NeusoftSpider",
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
) -> dict:
    """顺序下载爬取阶段发现的所有附件并入库，单文件失败不中断整轮。

    Returns:
        统计 {"found","downloaded","saved","skipped_md5","skipped_dup","failed"}
    """
    stats = {
        "found": len(spider.file_links), "downloaded": 0, "saved": 0,
        "skipped_md5": 0, "skipped_dup": 0, "failed": 0,
    }
    seen_md5: Set[str] = set()

    from apps.knowledge.models import KnowledgeDoc

    for link in spider.file_links[: spider.max_files]:
        if stats["saved"] >= spider.max_files:
            break
        try:
            # 下载前先按 source_url 查库去重，避免重复下载大文件
            if KnowledgeDoc.objects.filter(source_url=link["url"][:1024]).exists():
                stats["skipped_dup"] += 1
                continue

            time.sleep(CRAWL_DELAY)  # 礼貌限速，逐个顺序下载
            data = spider.download_file(link)
            if not data:
                stats["failed"] += 1
                continue
            stats["downloaded"] += 1

            if data["md5"] in seen_md5:
                stats["skipped_md5"] += 1
                continue
            if save_file_as_knowledge_doc(
                data,
                uploader_id=uploader_id,
                college_id=college_id,
                discipline_id=discipline_id,
            ):
                seen_md5.add(data["md5"])
                stats["saved"] += 1
            else:
                stats["skipped_dup"] += 1  # save 内部已按 URL/MD5 去重记日志
        except Exception as e:  # 单附件异常不中断整轮
            logger.error(f"附件处理异常: {link.get('url')} - {e}")
            stats["failed"] += 1

    logger.info(f"附件下载统计: {stats}")
    return stats


def crawl_and_index(
    max_pages: int = 50,
    uploader_id: int = 1,
    college_id: Optional[int] = None,
    discipline_id: int = None,
    download_files: Optional[bool] = None,
    file_types: Optional[str] = None,
    max_files: Optional[int] = None,
) -> int:
    """全站爬取并自动索引到知识库。

    Args:
        max_pages: 本次爬取最大页数
        uploader_id: 上传者用户 ID
        college_id: 所属学院 ID
        discipline_id: 学科方向 ID（可选，默认自动推断）
        download_files: 是否下载文档类附件（None=取设置 CRAWLER_DOWNLOAD_FILES）
        file_types: 附件扩展名白名单覆盖，逗号分隔（如 "pdf,docx"），None=取设置
        max_files: 本次最多下载附件数，None=取设置 CRAWLER_MAX_FILES

    Returns:
        成功保存的文档数（页面 + 附件）
    """
    if download_files is None:
        download_files = DOWNLOAD_FILES

    allowed_exts = None
    if file_types:
        allowed_exts = {t.strip().lower() for t in file_types.split(",") if t.strip()}

    spider = NeusoftSpider(
        download_files=download_files,
        allowed_exts=allowed_exts,
        max_files=max_files or MAX_FILES_PER_RUN,
    )
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

    # 页面爬取完成后统一下载附件入库
    if download_files:
        stats = download_and_save_files(
            spider,
            uploader_id=uploader_id,
            college_id=college_id,
            discipline_id=discipline_id,
        )
        saved += stats["saved"]

    logger.info(f"爬取并索引完成: 共爬取 {len(pages)} 页，保存 {saved} 篇文档（含附件）")
    return saved
