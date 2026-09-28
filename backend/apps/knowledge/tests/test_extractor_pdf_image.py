"""P0：PDF + 图片内容解析回归测试。

覆盖范围（对应审计报告 P0-1 ~ P0-5）：
- PDF 逐页判定：文字页走原生文字、扫描页单独 OCR、混合 PDF 不丢扫描页
- 页码型扫描 PDF 不得因页码累计超阈值被误判为文字 PDF
- OCR_MAX_PAGES 超限必须标记 truncated，不能静默当完整成功
- OCR 通道不可用（依赖缺失）必须显式失败，不被宽 except 吞成空串
- 图片（jpg/jpeg/png/webp/bmp/tif/gif）走 OCR，不再被当 UTF-8 读成乱码
- 二进制/未知扩展名不得按文本读取
- 解析为空 → status=failed + index_error，且不 Embedding / 不写 Milvus / 不写 DocChunk

依赖说明：
- PDF 用例需要 PyMuPDF（requirements.txt 已含），未安装时整体 skip；
- OCR 一律注入假通道（mock `_get_easyocr_reader`），不依赖 easyocr，
  保证无 OCR 环境的本地/CI 也能完整跑通解析逻辑。
"""
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from django.test import SimpleTestCase, TestCase
from PIL import Image

# 够长，能通过单页最小字数阈值
_SAMPLE_TEXT = '校园网使用说明：本页包含足够长度的正文，用于验证原生文字提取路径。'
_OCR_TEXT = '这是扫描页 OCR 识别出的中文文字'


def _fitz_available() -> bool:
    try:
        import fitz  # noqa: F401
    except ImportError:
        return False
    return True


requires_fitz = unittest.skipUnless(_fitz_available(), 'PyMuPDF 未安装，跳过 PDF 解析用例')


class _FakeReader:
    """假 OCR 通道：记录收到的像素数组，返回固定文字。

    真实环境里这个位置是 easyocr.Reader（`_get_easyocr_reader` 的懒加载单例）。
    """

    def __init__(self, text: str = _OCR_TEXT):
        self.text = text
        self.arrays = []

    def readtext(self, arr, detail=0, paragraph=True):
        self.arrays.append(arr)
        return [self.text] if self.text else []


class _ParserHarness(SimpleTestCase):
    """临时目录 + 假 OCR 通道 + PDF/图片构造工具。"""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix='p0_parse_')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

        self.reader = _FakeReader()
        # 注入假通道：所有 OCR 都走这里，测试不依赖 easyocr
        patcher = mock.patch(
            'apps.knowledge.extractor._get_easyocr_reader',
            return_value=self.reader,
        )
        self.get_reader = patcher.start()
        self.addCleanup(patcher.stop)

    # ---------------- 构造素材 ----------------
    def path(self, name: str) -> str:
        return os.path.join(self.tmpdir, name)

    def make_pdf(self, name: str, pages) -> str:
        """按页面类型构造 PDF：'text' 文字页 / 'num' 只有页码的页 / 'scan' 扫描页。"""
        import fitz

        doc = fitz.open()
        for kind in pages:
            page = doc.new_page()
            if kind == 'text':
                page.insert_text((72, 72), _SAMPLE_TEXT, fontname='china-s', fontsize=12)
            elif kind == 'num':
                page.insert_text((72, 72), '1', fontname='china-s', fontsize=12)
            elif kind == 'scan':
                page.insert_image(
                    fitz.Rect(50, 50, 350, 150), stream=self.image_bytes('PNG'),
                )
            else:
                raise ValueError(f'未知页面类型: {kind}')
        target = self.path(name)
        doc.save(target)
        doc.close()
        return target

    def image_bytes(self, fmt: str = 'PNG', size=(400, 150), color=(255, 255, 255)) -> bytes:
        buf = io.BytesIO()
        Image.new('RGB', size, color).save(buf, fmt)
        return buf.getvalue()

    def make_image(self, name: str, fmt: str = 'PNG', size=(400, 150)) -> str:
        target = self.path(name)
        Image.new('RGB', size, (255, 255, 255)).save(target, fmt)
        return target


# ------------------------------------------------------------------
# PDF：逐页判定
# ------------------------------------------------------------------
@requires_fitz
class TestPdfPerPageParsing(_ParserHarness):
    def test_text_pdf_uses_native_text_without_ocr(self):
        """文字型 PDF：全页走原生文字，一次 OCR 都不该发生。"""
        from apps.knowledge.extractor import _parse_pdf

        result = _parse_pdf(self.make_pdf('text.pdf', ['text', 'text']))

        self.assertEqual(result.pages_total, 2)
        self.assertEqual(result.pages_native, 2)
        self.assertEqual(result.pages_ocr, 0)
        self.assertIn('校园网使用说明', result.text)
        self.assertFalse(result.truncated)
        self.assertEqual(result.issues(), '')
        self.assertEqual(self.reader.arrays, [])

    def test_scanned_pdf_uses_ocr_per_page(self):
        """纯扫描 PDF：逐页渲染为图后 OCR，页序保持。"""
        from apps.knowledge.extractor import _parse_pdf

        result = _parse_pdf(self.make_pdf('scan.pdf', ['scan', 'scan']))

        self.assertEqual(result.pages_native, 0)
        self.assertEqual(result.pages_ocr, 2)
        self.assertEqual(result.text, f'{_OCR_TEXT}\n{_OCR_TEXT}')
        # 送进 OCR 的是真实渲染出来的三通道像素
        self.assertEqual(len(self.reader.arrays), 2)
        self.assertEqual(self.reader.arrays[0].shape[2], 3)

    def test_mixed_pdf_keeps_scanned_pages(self):
        """P0-2 核心回归：文字页 + 扫描页，扫描页内容必须进最终文本。

        改造前：全文文字层 >= 阈值就直接 return，扫描页永久丢失。
        """
        from apps.knowledge.extractor import _parse_pdf

        result = _parse_pdf(self.make_pdf('mixed.pdf', ['text', 'scan', 'text', 'scan']))

        self.assertEqual(result.pages_native, 2)
        self.assertEqual(result.pages_ocr, 2)
        self.assertEqual(result.text.count(_OCR_TEXT), 2)
        # 文字页内容与扫描页内容并存，且顺序为 文字/扫描/文字/扫描
        self.assertIn('校园网使用说明', result.text)
        self.assertLess(
            result.text.index('校园网使用说明'), result.text.index(_OCR_TEXT),
        )
        self.assertEqual(result.issues(), '')

    def test_page_number_only_pdf_is_not_treated_as_text_pdf(self):
        """页码型扫描件：页码累计超过阈值也不得被判成文字 PDF。"""
        from apps.knowledge.extractor import _parse_pdf

        result = _parse_pdf(self.make_pdf('nums.pdf', ['num'] * 5))

        # 没有任何一页被当成「有足够文字」
        self.assertEqual(result.pages_native, 0)
        self.assertEqual(result.pages_ocr, 5)
        # 正文完全来自 OCR，页码没有冒充正文
        self.assertEqual(result.text, '\n'.join([_OCR_TEXT] * 5))

    def test_ocr_budget_exhaustion_marks_truncated(self):
        """P0-5：超过 OCR_MAX_PAGES 不得静默成功。"""
        from apps.knowledge.extractor import _parse_pdf

        result = _parse_pdf(self.make_pdf('big.pdf', ['scan'] * 25))

        self.assertEqual(result.pages_total, 25)
        self.assertTrue(result.truncated)
        self.assertEqual(result.pages_ocr, 20)          # OCR_MAX_PAGES 默认 20
        self.assertEqual(result.pages_ocr_skipped, 5)
        self.assertIn('OCR_MAX_PAGES', result.issues())
        self.assertEqual(len(self.reader.arrays), 20)   # 没有超预算调用 OCR

    def test_ocr_unavailable_reports_dependency_error(self):
        """P0-4：OCR 通道不可用必须显式报错，而不是静默返回空文本。"""
        from apps.knowledge.extractor import _parse_pdf

        self.get_reader.return_value = None
        result = _parse_pdf(self.make_pdf('scan.pdf', ['scan']))

        self.assertTrue(result.is_empty)
        self.assertEqual(result.pages_ocr_failed, 1)
        self.assertIn('EasyOCR', result.error)
        self.assertIn('EasyOCR', result.issues())

    def test_broken_pdf_reports_error(self):
        """损坏文件必须带原因失败，而不是抛异常或返回空串。"""
        from apps.knowledge.extractor import _parse_pdf

        broken = self.path('broken.pdf')
        with open(broken, 'wb') as f:
            f.write(b'this is definitely not a pdf')

        result = _parse_pdf(broken)

        self.assertTrue(result.is_empty)
        self.assertNotEqual(result.error, '')

    def test_missing_pymupdf_reports_dependency_error(self):
        """fitz 缺失时返回明确错误，不把 ModuleNotFoundError 放出去。"""
        from apps.knowledge.extractor import _parse_pdf

        target = self.make_pdf('text.pdf', ['text'])   # 先构造，构造本身需要 fitz
        with mock.patch.dict('sys.modules', {'fitz': None}):
            result = _parse_pdf(target)

        self.assertTrue(result.is_empty)
        self.assertIn('PyMuPDF', result.error)


# ------------------------------------------------------------------
# 图片：OCR 解析
# ------------------------------------------------------------------
class TestImageParsing(_ParserHarness):
    def test_image_formats_go_through_ocr(self):
        """P0-1：图片必须走 OCR，各扩展名都要覆盖。"""
        from apps.knowledge.extractor import parse_file

        cases = (
            ('JPEG', 'jpg'), ('JPEG', 'jpeg'), ('PNG', 'png'), ('WEBP', 'webp'),
            ('BMP', 'bmp'), ('TIFF', 'tiff'), ('GIF', 'gif'),
        )
        for fmt, ext in cases:
            with self.subTest(ext=ext):
                self.reader.arrays.clear()
                result = parse_file(self.make_image(f'pic_{ext}.{ext}', fmt), ext)

                self.assertEqual(result.text, _OCR_TEXT)
                self.assertEqual(result.pages_ocr, 1)
                self.assertEqual(result.error, '')
                self.assertEqual(len(self.reader.arrays), 1)
                self.assertEqual(result.issues(), '')      # 完整解析无告警

    def test_transparent_png_decoded_as_rgb(self):
        """透明 PNG 需转 RGB 后再识别（不能因 alpha 通道失败）。"""
        from apps.knowledge.extractor import parse_file

        target = self.path('alpha.png')
        Image.new('RGBA', (300, 120), (255, 0, 0, 0)).save(target, 'PNG')

        result = parse_file(target, 'png')

        self.assertEqual(result.text, _OCR_TEXT)
        self.assertEqual(self.reader.arrays[0].shape[2], 3)

    def test_multiline_image_text_kept_in_order(self):
        """多行文字图片：行序不被打乱。"""
        from apps.knowledge.extractor import parse_file

        self.reader.text = '第一行文字\n第二行文字\n第三行文字'

        result = parse_file(self.make_image('multi.png'), 'png')

        self.assertEqual(
            result.text.splitlines(), ['第一行文字', '第二行文字', '第三行文字'],
        )

    def test_image_without_text_marks_failed_page(self):
        """纯色/无文字图片：OCR 通道正常但没识别出文字，必须计入失败页。"""
        from apps.knowledge.extractor import parse_file

        self.reader.text = ''

        result = parse_file(self.make_image('blank.png'), 'png')

        self.assertTrue(result.is_empty)
        self.assertEqual(result.pages_ocr_failed, 1)
        self.assertEqual(result.error, '')               # 不是通道故障
        self.assertIn('OCR 失败', result.issues())

    def test_ocr_unavailable_image(self):
        from apps.knowledge.extractor import parse_file

        self.get_reader.return_value = None

        result = parse_file(self.make_image('pic.png'), 'png')

        self.assertTrue(result.is_empty)
        self.assertIn('EasyOCR', result.error)

    def test_corrupt_image_reports_error(self):
        """损坏图片（PIL 解码失败）不能返回乱码文本。"""
        from apps.knowledge.extractor import parse_file

        target = self.path('bad.jpg')
        with open(target, 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0not-really-a-jpeg')

        result = parse_file(target, 'jpg')

        self.assertTrue(result.is_empty)
        self.assertNotEqual(result.error, '')

    def test_image_type_without_filename_extension_still_parsed(self):
        """file_name 无后缀但 file_type=image 时，仍要落到图片解析器。"""
        from apps.knowledge.extractor import parse_file, resolve_file_extension

        ext = resolve_file_extension('image', '无后缀文件名')
        self.assertEqual(ext, 'jpg')
        result = parse_file(self.make_image('noext.jpg'), ext)
        self.assertEqual(result.text, _OCR_TEXT)


# ------------------------------------------------------------------
# 扩展名解析 / 二进制阻断
# ------------------------------------------------------------------
@requires_fitz
class TestExtractorsRegistryContract(_ParserHarness):
    """钉住 views.py 上传路径的既有契约。

    `apps/knowledge/views.py` 的上传解析分支是
    `_EXTRACTORS.get(ext)(tmp_path)` 后直接 `parsed[:4000]`（字符串切片）。
    本次把 pdf/图片改成返回结构化 ParseResult，若注册表里漏包 `_as_text_parser`，
    上传接口会直接 TypeError —— 这里按 views 实际使用的类型逐一对齐。
    """

    def _upload_types(self):
        from apps.knowledge.views import FileUploadView

        return set(FileUploadView.DOCUMENT_EXTRACTABLE_TYPES)

    def test_registry_covers_all_upload_extractable_types(self):
        from apps.knowledge.extractor import _EXTRACTORS

        missing = sorted(e for e in self._upload_types() if not _EXTRACTORS.get(e))
        self.assertEqual(missing, [], f'上传接口使用的扩展名缺解析器: {missing}')

    def test_registry_returns_str_not_parseresult(self):
        """本次改造过的类型（pdf / 图片）在注册表里必须仍返回 str。"""
        from apps.knowledge.extractor import _EXTRACTORS

        pdf = self.make_pdf('contract.pdf', ['text'])
        self.assertIsInstance(_EXTRACTORS['pdf'](pdf), str, 'pdf 解析器必须返回 str（views 会切片）')
        self.assertIsInstance(_EXTRACTORS['jpg'](self.make_image('c.jpg', 'JPEG')), str)
        self.assertIsInstance(_EXTRACTORS['png'](self.make_image('c.png', 'PNG')), str)

    def test_image_extensions_all_registered(self):
        from apps.knowledge.extractor import _EXTRACTORS

        for ext in ('jpg', 'jpeg', 'png', 'webp', 'bmp', 'tif', 'tiff', 'gif'):
            self.assertIn(ext, _EXTRACTORS, f'图片扩展名 .{ext} 未注册')


class TestExtensionResolution(_ParserHarness):
    def test_image_extensions_resolve_to_real_extension(self):
        from apps.knowledge.extractor import resolve_file_extension

        self.assertEqual(resolve_file_extension('image', '校园平面图.jpg'), 'jpg')
        self.assertEqual(resolve_file_extension('image', 'a.JPEG'), 'jpeg')
        self.assertEqual(resolve_file_extension('image', 'a.png'), 'png')
        self.assertEqual(resolve_file_extension('image', 'a.webp'), 'webp')

    def test_pdf_still_resolves(self):
        from apps.knowledge.extractor import resolve_file_extension

        self.assertEqual(resolve_file_extension('pdf', '通知.pdf'), 'pdf')
        self.assertEqual(resolve_file_extension('pdf', ''), 'pdf')

    def test_binary_type_keeps_real_extension_for_error_message(self):
        """无解析器的二进制保留真实后缀，让失败原因能写清具体类型。"""
        from apps.knowledge.extractor import parse_file, resolve_file_extension

        self.assertEqual(resolve_file_extension('other', 'pack.zip'), 'zip')

        target = self.path('pack.zip')
        with open(target, 'wb') as f:
            f.write(b'PK\x03\x04binary')

        result = parse_file(target, 'zip')
        self.assertTrue(result.is_empty)
        self.assertIn('.zip', result.error)

    def test_ascii_unknown_extension_is_kept_as_real_extension(self):
        """ASCII 后缀（即使无解析器）按真实后缀处理，不回落到 file_type。"""
        from apps.knowledge.extractor import parse_file, resolve_file_extension

        # 无解析器的 ASCII 后缀：保留，交给 parse_file 报「暂不支持」
        self.assertEqual(resolve_file_extension('pdf', 'report.xyz'), 'xyz')
        self.assertEqual(resolve_file_extension('pdf', 'report.v2'), 'v2')
        self.assertEqual(resolve_file_extension('pdf', 'report.final'), 'final')

        archive = self.path('archive.zip')
        with open(archive, 'wb') as f:
            f.write(b'PK\x03\x04binary')

        ext = resolve_file_extension('zip', 'archive.zip')
        self.assertEqual(ext, 'zip')
        result = parse_file(archive, ext)
        self.assertEqual(result.text, '')
        self.assertIn('.zip', result.error)
        self.assertNotIn('pdf', result.error)

    def test_non_ascii_suffix_falls_back_to_file_type(self):
        """中文后缀不是扩展名：必须回落 file_type 默认值。

        回归背景：中文词同样通过 `isalnum()`，若不限 ASCII，`附件1.扫描件` 会被
        判成后缀「扫描件」→「暂不支持解析 .扫描件」→ 本该正常解析的 PDF 变 failed。
        """
        from apps.knowledge.extractor import parse_file, resolve_file_extension

        cases = (
            ('附件1.扫描件', 'pdf', 'pdf'),
            ('成绩单.正式', 'pdf', 'pdf'),
            ('报告.最终版', 'doc', 'doc'),
            ('材料.中文', 'txt', 'txt'),
            ('图片.扫描件', 'image', 'jpg'),     # image 无具体格式，回落默认 jpg
            ('附件1', 'pdf', 'pdf'),             # 无扩展名
            ('README', 'txt', 'txt'),
        )
        for file_name, file_type, expect in cases:
            with self.subTest(file_name=file_name, file_type=file_type):
                self.assertEqual(
                    resolve_file_extension(file_type, file_name), expect,
                    f'{file_name}（file_type={file_type}）应回落到 {expect}',
                )

        # 端到端：中文后缀的 PDF 必须真的进 PDF 解析器（失败原因来自 PDF，而非「不支持」）
        target = self.path('附件1.扫描件')
        with open(target, 'wb') as f:
            f.write(b'this is definitely not a pdf')
        result = parse_file(target, resolve_file_extension('pdf', '附件1.扫描件'))
        self.assertTrue(result.is_empty)
        self.assertNotIn('暂不支持', result.error)
        self.assertIn('PDF', result.error)

    def test_ascii_image_extensions_unaffected_by_f1(self):
        """F1 修复不得影响图片后缀解析。"""
        from apps.knowledge.extractor import resolve_file_extension

        for file_name, expect in (
            ('photo.jpg', 'jpg'), ('photo.jpeg', 'jpeg'),
            ('photo.png', 'png'), ('photo.webp', 'webp'),
            ('photo.bmp', 'bmp'), ('photo.tiff', 'tiff'), ('photo.gif', 'gif'),
        ):
            with self.subTest(file_name=file_name):
                self.assertEqual(resolve_file_extension('image', file_name), expect)


class _OssStubMixin:
    """把 OSS 下载替换成内存字节，让 `extract_text_from_oss` 走完整链路。"""

    def stub_oss_download(self, content: bytes):
        resp = mock.Mock()
        resp.content = content
        resp.raise_for_status = mock.Mock()

        p_url = mock.patch(
            'utils.oss.oss_client.get_download_url',
            return_value='https://oss.invalid/audit-probe',
        )
        p_get = mock.patch(
            'utils.http_client.ExternalClient.get', return_value=resp,
        )
        p_url.start()
        p_get.start()
        self.addCleanup(p_url.stop)
        self.addCleanup(p_get.stop)


class TestBinaryNeverReadAsText(_ParserHarness, _OssStubMixin):
    """P0-1 回归门禁：二进制内容永远不能变成正文。"""

    # 改造前，JPEG 经 UTF-8 + errors='ignore' 读出的乱码里必然带这些特征
    BINARY_MARKERS = ('JFIF', 'IHDR', 'Exif', '\x00')

    def test_image_bytes_never_leak_binary_markers(self):
        from apps.knowledge.extractor import extract_text_from_oss

        for fmt, ext in (('JPEG', 'jpg'), ('PNG', 'png'), ('WEBP', 'webp')):
            with self.subTest(ext=ext):
                self.stub_oss_download(self.image_bytes(fmt))

                text = extract_text_from_oss('knowledge/x', 'image', f'pic.{ext}')

                self.assertEqual(text, _OCR_TEXT)      # 只能是 OCR 结果
                for marker in self.BINARY_MARKERS:
                    self.assertNotIn(marker, text)

    def test_unsupported_binary_returns_empty_not_garbage(self):
        """zip/psd/mp4 等无解析器的二进制：结果必须为空，不能是乱码。"""
        from apps.knowledge.extractor import extract_text_from_oss

        samples = {
            'zip': b'PK\x03\x04\x14\x00\x00\x00\x08\x00binary-payload\xff\xfe\x00',
            'psd': b'8BPS\x00\x01\x00\x00\x00\x00\x00\x00binary-payload\xff\xfe\x00',
            'mp4': b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00binary-payload\xff\xfe',
        }
        for ext, payload in samples.items():
            with self.subTest(ext=ext):
                self.stub_oss_download(payload)

                text = extract_text_from_oss(f'knowledge/x.{ext}', 'other', f'x.{ext}')

                self.assertEqual(text, '')

    def test_detailed_entry_explains_unsupported_type(self):
        from apps.knowledge.extractor import extract_text_from_oss_detailed

        self.stub_oss_download(b'PK\x03\x04binary-payload')

        result = extract_text_from_oss_detailed('knowledge/x.zip', 'other', 'x.zip')

        self.assertTrue(result.is_empty)
        self.assertIn('不支持', result.error)

    def test_image_parse_failure_is_reported_not_indexable(self):
        """图片 OCR 不可用时，解析层必须给出可判失败的结果。"""
        from apps.knowledge.extractor import extract_text_from_oss_detailed

        self.get_reader.return_value = None
        self.stub_oss_download(self.image_bytes('JPEG'))

        result = extract_text_from_oss_detailed('knowledge/x.jpg', 'image', 'x.jpg')

        self.assertTrue(result.is_empty)
        self.assertIn('EasyOCR', result.error)


# ------------------------------------------------------------------
# 索引任务：失败可见化
# ------------------------------------------------------------------
class TestIndexTaskFailureVisibility(TestCase):
    """索引任务的失败可见化 + 审核门 fail-closed。

    两组契约（P1-2D STEP 4-B 之后）：

    1. **审核门**：`check_indexable()` 要求文档同时满足
       "对象在 `knowledge/` 下" + "有 ContentModeration 记录" +
       "`state ∈ STORAGE_ALLOWED_MODERATION_STATES`" + "`storage_state=promoted`"。
       不满足 → 任务判 `failed`，且**不解析、不 Embedding、不写 DocChunk、
       不进 Milvus**。因此下文的夹具必须给出 `approved + promoted` 的审核
       记录，否则测到的是审核门而不是解析失败可见化（门禁本身不做任何放宽）。
    2. **解析结果可见化**：解析为空 → `failed` + `index_error`；截断/OCR
       跳过 → 仍索引但必须留痕。
    """

    def setUp(self):
        from apps.accounts.models import User

        self.user = User.objects.create_user(
            username='p0_index_admin', password='testpass123', role='super_admin',
        )

    def _approve(
        self,
        doc,
        *,
        state=None,
        storage_state=None,
    ):
        """为文档补上审核记录（默认 approved + promoted = 应可通过索引门）。"""
        from apps.knowledge.models import ContentModeration
        from apps.knowledge.moderation import (
            MODERATION_STATE_APPROVED,
            STORAGE_STATE_PROMOTED,
            VERDICT_CLEAN,
        )

        state = MODERATION_STATE_APPROVED if state is None else state
        storage_state = STORAGE_STATE_PROMOTED if storage_state is None else storage_state
        ContentModeration.objects.create(
            doc=doc,
            state=state,
            verdict=VERDICT_CLEAN,
            storage_state=storage_state,
            # promoted_key 只对"已提升"才有意义，未提升时留空
            promoted_key=doc.oss_key if storage_state == STORAGE_STATE_PROMOTED else '',
        )
        return doc

    def _make_doc(self, *, with_moderation=True, **overrides):
        """构造待索引文档。

        with_moderation=True（默认）时同时写入 approved + promoted 的
        ContentModeration：这些用例验证的是"过了审核门之后"的解析层行为，
        不是审核门本身；审核门本身由 with_moderation=False 的用例验证。
        """
        from apps.knowledge.models import KnowledgeDoc

        defaults = dict(
            title='解析失败样本',
            description='描述不应被当作正文索引',
            source_type='upload',
            file_name='scan.pdf',
            file_type='pdf',
            oss_key='knowledge/test/scan.pdf',
            uploader=self.user,
            status='uploaded',
        )
        defaults.update(overrides)
        doc = KnowledgeDoc.objects.create(**defaults)
        if with_moderation:
            self._approve(doc)
        return doc

    def _run_index(self, doc_id: int):
        """同步执行索引任务：真实 Celery 用 .apply()，本地无 Celery 用 .delay()。"""
        from apps.knowledge.tasks import index_document_task

        if hasattr(index_document_task, 'apply'):
            return index_document_task.apply(args=(doc_id,), throw=True)
        return index_document_task.delay(doc_id)

    def _patch_downstream(self):
        """拦截 Embedding / Milvus，返回一组 mock 供断言。"""
        from utils.llm import llm_client
        from utils.milvus import milvus_client

        patches = [
            mock.patch.object(llm_client, 'embed',
                              side_effect=lambda texts, **kw: [[0.1] * 8 for _ in texts]),
            mock.patch.object(milvus_client, 'insert_chunks',
                              side_effect=lambda chunks, embs: [c['vector_id'] for c in chunks]),
            mock.patch.object(milvus_client, 'delete_by_doc'),
            mock.patch.object(milvus_client, 'ensure_collection', return_value=True),
        ]
        started = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        return started   # [embed, insert_chunks, delete_by_doc, ensure_collection]

    def test_no_moderation_record_is_not_indexed(self):
        """无 ContentModeration 记录 → fail-closed。

        P1-2D STEP 4-B 契约：审核记录缺失必须判为不可索引（历史文档语义），
        且整条索引链一步都不能走：不解析、不 Embedding、不写 DocChunk、
        不进 Milvus。这里**不能**放宽成"没有记录就默认放行"。
        """
        from apps.knowledge.extractor import ParseResult
        from apps.knowledge.models import DocChunk

        doc = self._make_doc(with_moderation=False)

        m_embed, m_insert, m_delete, m_ensure = self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=ParseResult(text='正常正文内容。' * 60, pages_total=1, pages_native=1),
        ) as m_parse:
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('审核记录', doc.index_error)
        self.assertIsNotNone(doc.failed_at)

        m_parse.assert_not_called()          # 未过门 → 解析器不得被调用
        m_embed.assert_not_called()          # 不得进入 Embedding
        m_insert.assert_not_called()         # 不得写入 Milvus
        m_delete.assert_not_called()
        m_ensure.assert_not_called()
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)
        self.assertEqual(doc.chunk_count, 0)

    def test_promoted_is_required_even_when_approved(self):
        """已审核但未提升（storage_state=quarantine）→ 同样不可索引。

        对象即使已经躺在 knowledge/ 前缀下，没有完成 promotion 也不是
        "可对外提供的内容"；门禁判据是 storage_state，不是路径本身。
        """
        from apps.knowledge.extractor import ParseResult
        from apps.knowledge.models import DocChunk
        from apps.knowledge.moderation import STORAGE_STATE_QUARANTINE

        doc = self._make_doc(
            with_moderation=False,
            oss_key='knowledge/test/scan.pdf',
        )
        self._approve(doc, storage_state=STORAGE_STATE_QUARANTINE)

        m_embed, m_insert, _, _ = self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=ParseResult(text='正常正文内容。' * 60, pages_total=1, pages_native=1),
        ) as m_parse:
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('提升', doc.index_error)
        m_parse.assert_not_called()
        m_embed.assert_not_called()
        m_insert.assert_not_called()
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)

    def test_empty_parse_fails_without_embedding_or_milvus(self):
        """P0-3：解析为空 → failed + index_error，不 Embedding / 不 Milvus / 无 DocChunk。"""
        from apps.knowledge.extractor import ParseResult
        from apps.knowledge.models import DocChunk

        doc = self._make_doc()
        parsed = ParseResult(error='暂不支持解析 .zip 类型（二进制文件不按文本读取）')

        m_embed, m_insert, m_delete, m_ensure = self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed', return_value=parsed,
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertIn('解析失败', doc.index_error)
        self.assertIn('不支持', doc.index_error)
        self.assertIsNotNone(doc.failed_at)

        m_embed.assert_not_called()
        m_insert.assert_not_called()
        m_delete.assert_not_called()
        m_ensure.assert_not_called()
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)

    def test_metadata_is_not_used_as_body_when_parse_empty(self):
        """metadata（标题/描述/文件名）不得冒充正文建立索引。"""
        from apps.knowledge.extractor import ParseResult
        from apps.knowledge.models import DocChunk, Keyword

        doc = self._make_doc(title='校园网通知', description='一段描述')
        Keyword.objects.create(doc=doc, phrase='校园网', weight=1, created_by=self.user)

        m_embed, m_insert, _, _ = self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed',
            return_value=ParseResult(error='图片 OCR 不可用'),
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'failed')
        self.assertEqual(doc.chunk_count, 0)
        self.assertEqual(DocChunk.objects.filter(doc=doc).count(), 0)
        m_embed.assert_not_called()
        m_insert.assert_not_called()

    def test_partial_parse_indexes_but_records_warning(self):
        """P0-5 可见化：截断/OCR 失败页仍索引，但必须留痕在 index_error。"""
        from apps.knowledge.extractor import ParseResult

        doc = self._make_doc(file_name='big_scan.pdf')
        parsed = ParseResult(
            text='扫描件正文内容。' * 60,
            pages_total=25, pages_ocr=20, pages_ocr_skipped=5, truncated=True,
        )

        m_embed, m_insert, _, _ = self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed', return_value=parsed,
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'indexed')
        self.assertIn('OCR_MAX_PAGES', doc.index_error)
        self.assertGreater(doc.chunk_count, 0)
        m_embed.assert_called_once()
        m_insert.assert_called_once()

    def test_clean_parse_leaves_no_error(self):
        """完整解析：状态 indexed 且 index_error 干净。"""
        from apps.knowledge.extractor import ParseResult
        from apps.knowledge.models import DocChunk

        doc = self._make_doc(file_name='normal.pdf', file_type='pdf')
        parsed = ParseResult(text='正常正文内容。' * 60, pages_total=1, pages_native=1)

        self._patch_downstream()
        with mock.patch(
            'apps.knowledge.extractor.extract_text_from_oss_detailed', return_value=parsed,
        ):
            self._run_index(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, 'indexed')
        self.assertEqual(doc.index_error, '')
        self.assertGreater(doc.chunk_count, 0)
        self.assertEqual(
            DocChunk.objects.filter(doc=doc).count(), doc.chunk_count,
        )
