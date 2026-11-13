"""
知识库工具模块
- 滑动窗口分块（500字/块，100字重叠）
"""
import logging
from typing import List

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 默认分块参数
# ------------------------------------------------------------------
CHUNK_SIZE = 500       # 每块字符数
OVERLAP = 100          # 重叠字符数
# 小表格（记录数不超过该值）每个分块都重复表格概要行（含字段名）；
# 超过后不再重复，只标注行号范围，避免长表每块都背一份表头挤占上下文。
MAX_HEADER_REPEAT_RECORDS = 200


def _extract_headings(text: str) -> List[tuple]:
    """识别常见中文/ Markdown 标题，返回 [(位置, 层级, 标题), ...]。"""
    import re

    rules = [
        (re.compile(r'^第[一二三四五六七八九十百]+[章篇部分]\s*.{0,40}$'), 1),
        (re.compile(r'^[一二三四五六七八九十]+、\s*.{0,40}$'), 2),
        (re.compile(r'^（[一二三四五六七八九十]+）\s*.{0,40}$'), 3),
        (re.compile(r'^\d+(?:\.\d+)*[、.]?\s+\S.{0,40}$'), 4),
    ]
    headings = []
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            match = re.match(r'^(#{1,6})\s+(.{1,60})$', stripped)
            if match:
                headings.append((pos, len(match.group(1)), match.group(2).strip()))
            else:
                for pattern, level in rules:
                    if pattern.match(stripped):
                        headings.append((pos, level, stripped[:60]))
                        break
        pos += len(line)
    return headings


def _heading_path_at(headings: List[tuple], position: int) -> str:
    """返回指定位置生效的标题路径（各级取最近一个标题）。"""
    active = {}
    for pos, level, title in headings:
        if pos > position:
            break
        active[level] = title
        for deeper in [k for k in active if k > level]:
            active.pop(deeper, None)
    return ' > '.join(active[k] for k in sorted(active))


def _split_parents(text: str, parent_size: int) -> List[tuple]:
    """按 parent_size 切父块，尽量在段落/换行边界断开；返回 [(start, end), ...]。"""
    spans, start, text_len = [], 0, len(text)
    while start < text_len:
        end = min(start + parent_size, text_len)
        if end < text_len:
            # 在末尾 200 字符内回退到最近的换行，避免把句子劈开
            window_start = max(start + int(parent_size * 0.6), end - 200)
            cut = text.rfind('\n', window_start, end)
            if cut > start:
                end = cut + 1
        if text[start:end].strip():
            spans.append((start, end))
        if end >= text_len:
            break
        start = end
    return spans


def chunk_document(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
    parent_size: int = None,
) -> List[dict]:
    """父子块分块（阶段 1.2）。

    - 子块：chunk_size 滑动窗口（默认 350/80），进向量库用于"检得准"；
    - 父块：parent_size 段落对齐大块（默认 1200），只存 MySQL，检索命中子块后
      回填父块给 LLM，用于"答得全"；
    - 附带 heading_path（标题路径）与字符偏移，便于引用定位。

    Returns:
        [{"chunk_index", "content", "char_count", "chunk_level",
          "parent_index", "heading_path", "char_start", "char_end"}, ...]
        子块在前（chunk_level=1）、父块在后（chunk_level=0）。
    """
    if not text or not text.strip():
        return []

    parent_size = parent_size or max(chunk_size * 3, 1200)
    headings = _extract_headings(text)
    children, parents = [], []

    for p_start, p_end in _split_parents(text, parent_size):
        parent_text = text[p_start:p_end]
        parent_index = None  # 父块 chunk_index 在子块排完后才知道，先占位

        start = p_start
        while start < p_end:
            end = min(start + chunk_size, p_end)
            piece = text[start:end]
            if piece.strip():
                children.append({
                    'chunk_index': len(children),
                    'content': piece,
                    'char_count': len(piece),
                    'chunk_level': 1,
                    'parent_placeholder': len(parents),
                    'heading_path': _heading_path_at(headings, start),
                    'char_start': start,
                    'char_end': end,
                })
            if end >= p_end:
                break
            start = end - overlap
            if start <= p_start or start >= p_end:
                break

        parents.append({
            'content': parent_text,
            'char_start': p_start,
            'char_end': p_end,
            'heading_path': _heading_path_at(headings, p_start),
        })

    # 父块排在子块之后，回填子块的 parent_index
    base = len(children)
    for i, child in enumerate(children):
        child['parent_index'] = base + child.pop('parent_placeholder')
    for i, parent in enumerate(parents):
        parent.update({
            'chunk_index': base + i,
            'char_count': len(parent['content']),
            'chunk_level': 0,
            'parent_index': -1,
        })

    logger.info(
        '分块完成: 原文 %d 字符 → 子块 %d / 父块 %d (child=%d, parent=%d)',
        len(text), len(children), len(parents), chunk_size, parent_size,
    )
    return children + parents


def contextual_embed_text(title: str, chunk: dict) -> str:
    """低成本 Contextual Retrieval：Embedding 前拼接「文档标题 > 标题路径」。

    不做 LLM 生成上下文（完整版成本高、需按文档哈希缓存），只把已有结构化
    信息前置，让同义正文在不同章节下产生可区分的向量。存储内容不改变，
    只影响送入 Embedding 的文本，因此不影响引用展示与父块回填。
    """
    content = chunk.get('content') or ''
    parts = [p.strip() for p in (title, chunk.get('heading_path') or '') if (p or '').strip()]
    if not parts:
        return content
    return '【' + ' > '.join(parts) + '】\n' + content


def chunk_text_by_paragraphs(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> List[dict]:
    """按段落边界对齐的滑动窗口分块。

    优先以段落（\\n\\n）为分割边界，在不超过 chunk_size 的前提下
    合并完整段落，相邻块仍保留 overlap 字符重叠。

    Args:
        text: 文档全文
        chunk_size: 每块最大字符数（默认 500）
        overlap: 重叠字符数（默认 100）

    Returns:
        同 chunk_document() 格式
    """
    if not text or not text.strip():
        return []

    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = []
    current_len = 0
    index = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)

        if current_len + para_len <= chunk_size:
            current_chunk.append(para)
            current_len += para_len + 2  # +2 for "\n\n"
        else:
            if current_chunk:
                chunks.append({
                    'chunk_index': index,
                    'content': '\n\n'.join(current_chunk),
                    'char_count': current_len,
                })
                index += 1

            # 保留 overlap 内容用于下一块
            overlap_text = ''
            if overlap > 0 and current_chunk:
                # 从当前块末尾取 overlap 字符
                full_current = '\n\n'.join(current_chunk)
                overlap_text = full_current[-overlap:] if len(full_current) > overlap else full_current

            current_chunk = [overlap_text, para] if overlap_text else [para]
            current_len = len(overlap_text) + para_len + 2

    # 最后一块
    if current_chunk:
        chunks.append({
            'chunk_index': index,
            'content': '\n\n'.join(current_chunk),
            'char_count': current_len,
        })

    logger.info(
        '段落分块完成: 原文 %d 字符 → %d 块 (chunk_size=%d, overlap=%d)',
        len(text), len(chunks), chunk_size, overlap,
    )
    return chunks


def chunk_records(text: str, chunk_size: int = CHUNK_SIZE) -> List[dict]:
    """记录级分块：按行对齐打包，绝不把一行（一条记录）裁断。

    用于表格/名单类文档。每个分块由若干完整行拼成、总长不超过
    chunk_size；单行超长时单独成块（宁肯块偏大也不切断一条记录），
    字段语义不因分块而丢失。首行（表格概要，如「本表格共 N 条记录」）
    保留为块 0，供计数类问题直接取精确总数。

    表头与行号（阶段 1.2）：
    - 小表格（≤200 条记录）每个块都重复表格概要行（含字段名），
      使任意块的向量都带着"这张表是什么"的上下文，精确查询更易命中；
    - 大表格不重复概要（避免每块都背一份长表头），只在块首标注
      行号范围「（第 12-25 条记录，共 400 条）」，便于定位与人工核对。

    Returns:
        同 chunk_document() 格式的 [{chunk_index, content, char_count}, ...]
    """
    if not text or not text.strip():
        return []

    lines = [line.rstrip() for line in text.split('\n') if line.strip()]
    if not lines:
        return []

    # 首行是 _records_from_grid 生成的表格概要（含字段名），单独抽出
    summary = ''
    if lines[0].startswith('本表格共') or '字段：' in lines[0]:
        summary = lines[0]
        lines = lines[1:]

    total_records = len(lines)
    repeat_header = bool(summary) and 0 < total_records <= MAX_HEADER_REPEAT_RECORDS
    header_len = (len(summary) + 1) if repeat_header else 0

    def build(index: int, buf: List[str], row_start: int, row_end: int) -> dict:
        parts = []
        if summary and (index == 0 or repeat_header):
            parts.append(summary)
        if total_records:
            parts.append(f'（第 {row_start}-{row_end} 条记录，共 {total_records} 条）')
        parts.extend(buf)
        content = '\n'.join(parts)
        return {
            'chunk_index': index,
            'content': content,
            'char_count': len(content),
        }

    chunks: List[dict] = []
    buf: List[str] = []
    buf_len = 0
    row_start = 1
    index = 0

    for row_no, line in enumerate(lines, start=1):
        line_len = len(line) + 1  # +1 换行符
        if buf and buf_len + header_len + line_len > chunk_size:
            chunks.append(build(index, buf, row_start, row_no - 1))
            index += 1
            buf = []
            buf_len = 0
            row_start = row_no
        buf.append(line)
        buf_len += line_len

    if buf:
        chunks.append(build(index, buf, row_start, total_records))

    logger.info(
        '记录级分块完成: 原文 %d 字符 → %d 块 (chunk_size=%d, 表头重复=%s)',
        len(text), len(chunks), chunk_size, repeat_header,
    )
    return chunks
