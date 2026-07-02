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


def chunk_document(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> List[dict]:
    """滑动窗口分块。

    将长文本按滑动窗口切分成多个短片段，
    相邻块之间保留 overlap 字符重叠，避免关键信息在边界被切断。

    Args:
        text: 文档全文
        chunk_size: 每块最大字符数（默认 500）
        overlap: 相邻块重叠字符数（默认 100）

    Returns:
        [{"chunk_index": int, "content": str, "char_count": int}, ...]

    示例：
        >>> chunk_document("a" * 1200, chunk_size=500, overlap=100)
        # 块1: [0:500], 块2: [400:900], 块3: [800:1200]
    """
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    index = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk_text = text[start:end]

        # 跳过纯空白块
        if chunk_text.strip():
            chunks.append({
                'chunk_index': index,
                'content': chunk_text,
                'char_count': len(chunk_text),
            })
            index += 1

        if end == text_len:
            break

        # 滑动窗口：下一块起点 = 本块终点 - 重叠
        start = end - overlap
        if start >= text_len:
            break

    logger.info(
        '分块完成: 原文 %d 字符 → %d 块 (chunk_size=%d, overlap=%d)',
        text_len, len(chunks), chunk_size, overlap,
    )
    return chunks


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
