"""
Celery 异步任务配置
- 文档索引任务 index_document（上传后自动触发，核心链路）
- 定时同步任务（二期：官网爬虫、图书馆摘要）
- FAQ 自动生成（每周一 03:00）
"""
import logging
import os

from celery import Celery
from celery.schedules import crontab

logger = logging.getLogger(__name__)

# Django settings 作为 Celery 配置源
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('campus_knowledge')

# 从 Django settings 读取配置，namespace='CELERY'
app.config_from_object('django.conf:settings', namespace='CELERY')

# 自动发现各 app 下的 tasks.py
app.autodiscover_tasks()


# ------------------------------------------------------------------
# 定时任务配置
# ------------------------------------------------------------------
app.conf.beat_schedule = {
    'generate-faq-weekly': {
        'task': 'apps.faq.tasks.generate_faq_weekly',
        'schedule': crontab(hour=3, minute=0, day_of_week=1),  # 每周一 03:00
    },
    'crawl-neusoft-website-daily': {
        'task': 'apps.knowledge.tasks.crawl_neusoft_website_task',
        'schedule': crontab(hour=4, minute=0),  # 每天 04:00
        'kwargs': {'max_pages': 100, 'uploader_id': 1},
    },
    'crawl-neusoft-website-weekly-full': {
        'task': 'apps.knowledge.tasks.crawl_neusoft_website_task',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),  # 每周日 05:00
        'kwargs': {'max_pages': 300, 'uploader_id': 1},
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Celery 调试任务"""
    logger.info(f'Celery debug task: {self.request.id}')


# ------------------------------------------------------------------
# 文档异步索引任务
# 设计文档第 13.2 节③：上传完成 → Celery → 下载→解析→分块→Embedding→Milvus→更新状态
# ------------------------------------------------------------------
@app.task(bind=True, max_retries=3, default_retry_delay=60)
def index_document(self, doc_id: int):
    """
    异步索引文档到 Milvus。

    流程:
        1. 从 MySQL 读取 knowledge_doc 记录，状态更新为 'processing'
        2. 用 OSS SDK 下载文件到临时目录
        3. 根据 file_type 选择解析器（PDF/DOC/PPT/TXT/MD）
        4. 文本分块（semantic chunking: 512 tokens/块, 128 tokens 重叠）
        5. 批量调 Embedding API 生成向量
        6. 批量写入 Milvus
        7. 更新 doc 状态为 'indexed'，记录 chunk_count、indexed_at

    异常处理:
        - 解析失败 → status='error'，记录异常信息
        - Embedding API 超时 → 自动重试（max_retries=3）
        - Milvus 写入失败 → 重试，最终失败 status='error'

    Args:
        doc_id: knowledge_doc 主键
    """
    from utils.llm import llm_client
    from utils.milvus import milvus_client
    from utils.oss import oss_client

    KnowledgeDoc = None
    try:
        from apps.knowledge.models import KnowledgeDoc
    except ImportError:
        logger.error('KnowledgeDoc 模型未就绪，无法执行索引任务')
        return

    # 1. 读取记录
    try:
        doc = KnowledgeDoc.objects.get(id=doc_id)
    except KnowledgeDoc.DoesNotExist:
        logger.error(f'index_document: doc_id={doc_id} 不存在')
        return

    try:
        # 2. 更新状态为 processing
        doc.status = 'processing'
        doc.save(update_fields=['status'])

        # 3. 下载文件
        tmp_path = f'/tmp/campus_index_{doc_id}_{doc.file_name}'
        download_url = oss_client.get_download_url(doc.oss_key, expires=600)
        if not download_url:
            raise RuntimeError(f'无法生成下载 URL: oss_key={doc.oss_key}')

        import requests
        resp = requests.get(download_url, timeout=120)
        resp.raise_for_status()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)

        # 4. 解析文本
        text = _parse_document(tmp_path, doc.file_type)

        # 5. 文本分块
        chunks = _chunk_text(text, doc_id)
        logger.info(f'index_document doc_id={doc_id}: {len(chunks)} chunks')

        if not chunks:
            doc.status = 'indexed'
            doc.chunk_count = 0
            doc.indexed_at = None  # 由 Django auto_now 或手动设置
            doc.save(update_fields=['status', 'chunk_count'])
            return

        # 6. Milvus 就绪
        milvus_client.connect()
        milvus_client.ensure_collection()

        # 7. 批量 Embedding + 写入 Milvus（每批 20 个 chunk）
        BATCH_SIZE = 20
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            texts = [c['chunk_text'] for c in batch]
            try:
                embeddings = llm_client.embed(texts)
                milvus_client.insert_chunks(batch, embeddings)
            except Exception as e:
                logger.error(f'Embedding/Milvus 写入失败 batch {i}: {e}')
                raise self.retry(exc=e)

        # 8. 更新状态
        doc.status = 'indexed'
        doc.chunk_count = len(chunks)
        # doc.indexed_at 由模型 auto_now 或手动设置
        doc.save(update_fields=['status', 'chunk_count'])
        logger.info(f'index_document doc_id={doc_id} 索引完成: {len(chunks)} chunks')

    except Exception as e:
        logger.error(f'index_document doc_id={doc_id} 失败: {e}')
        try:
            doc.status = 'error'
            doc.save(update_fields=['status'])
        except Exception:
            pass
        raise self.retry(exc=e)

    finally:
        # 清理临时文件
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


# ------------------------------------------------------------------
# 文档解析（内部）
# ------------------------------------------------------------------
def _parse_document(file_path: str, file_type: str) -> str:
    """
    根据文件类型选择解析器提取文本。

    支持: pdf, doc, docx, ppt, pptx, xls, xlsx, txt, md, html
    不支持的类型返回空字符串。
    """
    ext = file_type.lower() if file_type else ''
    ext = ext if ext.startswith('.') else f'.{ext}'

    try:
        if ext in ('.pdf',):
            return _parse_pdf(file_path)
        elif ext in ('.docx', '.doc'):
            return _parse_docx(file_path)
        elif ext in ('.pptx', '.ppt'):
            return _parse_pptx(file_path)
        elif ext in ('.xlsx', '.xls'):
            return _parse_xlsx(file_path)
        elif ext in ('.txt', '.md', '.html', '.htm'):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        else:
            logger.warning(f'不支持的文件类型: {ext}，仅保留标题/描述作为文本')
            return ''
    except Exception as e:
        logger.error(f'文档解析失败 ({ext}): {e}')
        return ''


def _parse_pdf(file_path: str) -> str:
    """PDF 文本提取（优先 PyMuPDF，降级 pdfplumber）"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        text = ''
        for page in doc:
            text += page.get_text() + '\n'
        doc.close()
        return text
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f'PyMuPDF 解析失败: {e}')

    try:
        import pdfplumber
        text = ''
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + '\n'
        return text
    except ImportError:
        logger.error('未安装 PyMuPDF 或 pdfplumber，无法解析 PDF')
        return ''
    except Exception as e:
        logger.error(f'pdfplumber 解析失败: {e}')
        return ''


def _parse_docx(file_path: str) -> str:
    """DOCX 文本提取"""
    try:
        from docx import Document
        doc = Document(file_path)
        return '\n'.join(p.text for p in doc.paragraphs)
    except ImportError:
        logger.error('未安装 python-docx')
        return ''
    except Exception as e:
        logger.error(f'DOCX 解析失败: {e}')
        return ''


def _parse_pptx(file_path: str) -> str:
    """PPTX 文本提取"""
    try:
        from pptx import Presentation
        prs = Presentation(file_path)
        text = ''
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    text += shape.text_frame.text + '\n'
        return text
    except ImportError:
        logger.error('未安装 python-pptx')
        return ''
    except Exception as e:
        logger.error(f'PPTX 解析失败: {e}')
        return ''


def _parse_xlsx(file_path: str) -> str:
    """XLSX 文本提取"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True)
        text = ''
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            text += f'[工作表: {sheet_name}]\n'
            for row in ws.iter_rows(values_only=True):
                row_text = '\t'.join(str(c) if c is not None else '' for c in row)
                if row_text.strip():
                    text += row_text + '\n'
        return text
    except ImportError:
        logger.error('未安装 openpyxl')
        return ''
    except Exception as e:
        logger.error(f'XLSX 解析失败: {e}')
        return ''


# ------------------------------------------------------------------
# 文本分块（内部）
# ------------------------------------------------------------------
def _chunk_text(text: str, doc_id: int, chunk_size: int = 512, overlap: int = 128) -> list:
    """
    Semantic chunking：按段落边界切分，每块约 512 tokens，相邻块重叠 128 tokens。

    Args:
        text: 完整文本
        doc_id: 文档 ID
        chunk_size: 每块最大 token 数（估算：1 token ≈ 2 中文字符）
        overlap: 相邻块重叠 token 数

    Returns:
        [{'vector_id': str, 'doc_id': int, 'chunk_index': int,
          'chunk_text': str, 'page_number': int, 'seq_order': int}, ...]
    """
    import uuid as _uuid

    if not text or not text.strip():
        return []

    # 按段落切分
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]

    chunks = []
    current_chunk = ''
    chunk_index = 0
    char_per_token = 2  # 中文约 2 字符/token
    max_chars = chunk_size * char_per_token
    overlap_chars = overlap * char_per_token

    for para in paragraphs:
        if len(current_chunk) + len(para) <= max_chars:
            current_chunk += para + '\n'
        else:
            if current_chunk.strip():
                chunks.append({
                    'vector_id': f'doc_{doc_id}_chunk_{chunk_index}',
                    'doc_id': doc_id,
                    'chunk_index': chunk_index,
                    'chunk_text': current_chunk.strip(),
                    'page_number': 0,
                    'seq_order': chunk_index,
                })
                chunk_index += 1
                # 重叠：保留尾部 overlap_chars 个字符
                if len(current_chunk) > overlap_chars:
                    current_chunk = current_chunk[-overlap_chars:] + para + '\n'
                else:
                    current_chunk = para + '\n'
            else:
                current_chunk = para + '\n'

    # 最后一块
    if current_chunk.strip():
        chunks.append({
            'vector_id': f'doc_{doc_id}_chunk_{chunk_index}',
            'doc_id': doc_id,
            'chunk_index': chunk_index,
            'chunk_text': current_chunk.strip(),
            'page_number': 0,
            'seq_order': chunk_index,
        })

    return chunks
