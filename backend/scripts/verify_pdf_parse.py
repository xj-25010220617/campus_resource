"""验证 PDF 解析与分块效果（复刻 extractor._parse_pdf / celery._chunk_text 逻辑）

用法: python verify_pdf_parse.py <pdf路径>
输出: 页级统计、全文长度、分块统计、名单在各块的分布
"""
import sys
import re

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else r'd:\实验室204\校园虚拟资源管理\测试文件\校发[2026]64号-关于表彰2025-2026年度优秀教师、优秀辅导员、优秀教育工作者和教育工作先进集体的决定.pdf'

PAGE_MIN_CHARS = 20   # 与 settings.OCR_PAGE_MIN_TEXT_CHARS 默认一致
CHUNK_SIZE = 512      # token
OVERLAP = 128         # token
CHAR_PER_TOKEN = 2
MAX_CHARS = CHUNK_SIZE * CHAR_PER_TOKEN
OVERLAP_CHARS = OVERLAP * CHAR_PER_TOKEN


def parse_pdf(file_path):
    """复刻 extractor._parse_pdf 的逐页判定逻辑（本地无 easyocr 时只统计 OCR 页数）"""
    import fitz
    doc = fitz.open(file_path)
    parts = []
    stats = {'total': doc.page_count, 'native': 0, 'native_chars': 0,
             'ocr_needed': 0, 'ocr_needed_chars': 0}
    for page_no, page in enumerate(doc, start=1):
        native = (page.get_text() or '').strip()
        if len(native) >= PAGE_MIN_CHARS:
            stats['native'] += 1
            stats['native_chars'] += len(native)
            parts.append(native)
        else:
            stats['ocr_needed'] += 1
            stats['ocr_needed_chars'] += len(native)
            parts.append(f'[第{page_no}页-文字层不足{len(native)}字-需OCR]')
    doc.close()
    return '\n'.join(parts), stats


def chunk_text(text):
    """复刻 celery._chunk_text"""
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks = []
    current = ''
    for para in paragraphs:
        if len(current) + len(para) <= MAX_CHARS:
            current += para + '\n'
        else:
            if current.strip():
                chunks.append(current.strip())
                if len(current) > OVERLAP_CHARS:
                    current = current[-OVERLAP_CHARS:] + para + '\n'
                else:
                    current = para + '\n'
            else:
                current = para + '\n'
    if current.strip():
        chunks.append(current.strip())
    return chunks


def main():
    text, stats = parse_pdf(PDF_PATH)
    print('=' * 70)
    print('【页级解析统计】与线上 extractor._parse_pdf 同逻辑')
    print(f'  总页数: {stats["total"]}')
    print(f'  原生文字层页数: {stats["native"]}（共 {stats["native_chars"]} 字符）')
    print(f'  需 OCR 页数: {stats["ocr_needed"]}（文字层残留 {stats["ocr_needed_chars"]} 字符）')
    print(f'  解析总文本: {len(text)} 字符')
    print()

    chunks = chunk_text(text)
    print('【分块统计】chunk=512token/1024字符, overlap=128token/256字符')
    print(f'  总块数: {len(chunks)}')
    for i, c in enumerate(chunks):
        print(f'  chunk {i}: {len(c)} 字符, 开头: {c[:40]!r}')
    print()

    # 名单完整性分析：把疑似人名的文本按块统计
    print('【人名分布分析】（每块中文名计数）')
    name_re = re.compile(r'[\u4e00-\u9fa5]{2,4}(?= [\u4e00-\u9fa5]{1,2}$|$)')
    total_names = 0
    for i, c in enumerate(chunks):
        # 简单启发：2-4字中文、且行内多为连续人名（用顿号/空格分隔的行）
        lines = [l for l in c.split('\n') if l.strip()]
        name_lines = [l for l in lines if re.search(r'[\u4e00-\u9fa5]{2,4}([\s、,，]+[\u4e00-\u9fa5]{2,4}){2,}', l)]
        cnt = 0
        for l in name_lines:
            cnt += len(re.findall(r'[\u4e00-\u9fa5]{2,4}(?=[\s、,，]|$)', l))
        total_names += cnt
        if cnt:
            print(f'  chunk {i}: 名单行 {len(name_lines)} 行, 疑似人名 {cnt} 个')
    print(f'  全文疑似人名总数: ~{total_names}')

    # 模拟 RAG 上下文截断：top5 块 + CONTEXT_MAX_CHARS=8000
    print()
    print('【RAG 上下文截断模拟】_enrich_with_doc_meta 最多取 5 块, CONTEXT_MAX_CHARS=8000')
    ctx_chars = 0
    used = 0
    for i, c in enumerate(chunks[:5]):
        block = len(f'### 参考资料{i}（来源：xxx）\n{c}')
        if ctx_chars + block > 8000:
            break
        ctx_chars += block
        used += 1
    print(f'  可进入 LLM 上下文: {used}/{len(chunks)} 块（共 {ctx_chars} 字符）')
    if len(chunks) > used:
        missing = chunks[used:]
        miss_names = sum(len(re.findall(r'[\u4e00-\u9fa5]{2,4}(?=[\s、,，]|$)', l))
                         for c in missing for l in c.split('\n')
                         if re.search(r'[\u4e00-\u9fa5]{2,4}([\s、,，]+[\u4e00-\u9fa5]{2,4}){2,}', l))
        print(f'  剩余 {len(chunks) - used} 块无法进入上下文，其中含 ~{miss_names} 个名单人名')

    # 展示全文前 300 字符 + 名单部分预览
    print()
    print('【全文预览（前 500 字符）】')
    print(text[:500])
    print('...')


if __name__ == '__main__':
    main()
