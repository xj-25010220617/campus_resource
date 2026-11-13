"""阶段 1.1 解析路由回归测试。

覆盖：
- OCR 后端可切换（rapidocr / easyocr），auto 时按可用性选择
- RapidOCR 不可用必须显式报错（不静默当空页）
- PARSER_BACKEND=markitdown：DOCX/PPTX 走 MarkItDown，失败/为空回落 legacy
- PARSER_BACKEND=service：PDF 走外部解析服务，未配置或失败回落本地
- XLSX 不受 markitdown 影响（表格结构化记录要喂 Text-to-SQL）

全部用假通道，不依赖 markitdown / rapidocr / 网络是否可用。
"""
import io
import os
import shutil
import sys
import tempfile
import types
from unittest import mock

from django.test import SimpleTestCase, override_settings
from PIL import Image

from apps.knowledge import extractor


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (40, 20), (255, 255, 255)).save(buf, 'PNG')
    return buf.getvalue()


class _FakeRapidOCR:
    def __init__(self, lines=('第一行', '第二行')):
        self.lines = lines

    def __call__(self, arr):
        return ([[[0, 0], [1, 0], [1, 1], [0, 1]], line, 0.99] for line in self.lines), None


class OCRBackendTests(SimpleTestCase):
    @override_settings(OCR_BACKEND='rapidocr')
    def test_rapidocr_backend_joins_lines_in_order(self):
        with mock.patch.object(extractor, '_get_rapidocr_engine', return_value=_FakeRapidOCR()):
            text, error = extractor._ocr_image_detailed(_png_bytes())

        self.assertEqual(text, '第一行\n第二行')
        self.assertEqual(error, '')

    @override_settings(OCR_BACKEND='rapidocr')
    def test_rapidocr_unavailable_reports_error(self):
        with mock.patch.object(extractor, '_get_rapidocr_engine', return_value=None):
            text, error = extractor._ocr_image_detailed(_png_bytes())

        self.assertEqual(text, '')
        self.assertIn('RapidOCR', error)

    @override_settings(OCR_BACKEND='rapidocr')
    def test_rapidocr_no_text_returns_empty_without_error(self):
        with mock.patch.object(extractor, '_get_rapidocr_engine', return_value=_FakeRapidOCR(lines=())):
            text, error = extractor._ocr_image_detailed(_png_bytes())

        self.assertEqual((text, error), ('', ''))

    @override_settings(OCR_BACKEND='easyocr')
    def test_easyocr_backend_still_selectable(self):
        fake_reader = mock.Mock()
        fake_reader.readtext.return_value = ['扫描文字']
        with mock.patch.object(extractor, '_get_easyocr_reader', return_value=fake_reader):
            text, error = extractor._ocr_image_detailed(_png_bytes())

        self.assertEqual((text, error), ('扫描文字', ''))

    def test_auto_prefers_rapidocr_when_available(self):
        with override_settings(OCR_BACKEND='auto'):
            # 只验证选择逻辑本身：rapidocr 存在则选 rapidocr
            with mock.patch('importlib.util.find_spec', return_value=object()):
                self.assertEqual(extractor._ocr_backend(), 'rapidocr')


class _FakeMarkItDown:
    text = '# 标题\n\n正文内容'

    def __init__(self, **kwargs):
        pass

    def convert(self, path):
        return types.SimpleNamespace(text_content=self.text)


def _fake_markitdown_module():
    module = types.ModuleType('markitdown')
    module.MarkItDown = _FakeMarkItDown
    return module


class ParserBackendTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix='stage1_router_')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _path(self, name: str, data: bytes = b'%PDF-1.4\n') -> str:
        target = os.path.join(self.tmpdir, name)
        with open(target, 'wb') as f:
            f.write(data)
        return target

    @override_settings(PARSER_BACKEND='markitdown')
    def test_markitdown_used_for_docx(self):
        with mock.patch.dict(sys.modules, {'markitdown': _fake_markitdown_module()}):
            result = extractor.parse_file('/tmp/a.docx', 'docx')

        self.assertEqual(result.text, '# 标题\n\n正文内容')

    @override_settings(PARSER_BACKEND='markitdown')
    def test_markitdown_empty_falls_back_to_legacy(self):
        module = _fake_markitdown_module()
        module.MarkItDown = lambda **kw: types.SimpleNamespace(
            convert=lambda path: types.SimpleNamespace(text_content='   '))
        legacy = mock.Mock(return_value=extractor.ParseResult(text='legacy 正文'))
        with mock.patch.dict(sys.modules, {'markitdown': module}):
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'docx': legacy}):
                result = extractor.parse_file('/tmp/a.docx', 'docx')

        self.assertEqual(result.text, 'legacy 正文')
        legacy.assert_called_once()

    @override_settings(PARSER_BACKEND='markitdown')
    def test_markitdown_not_applied_to_xlsx(self):
        module = _fake_markitdown_module()
        legacy = mock.Mock(return_value=extractor.ParseResult(text='表格'))
        with mock.patch.dict(sys.modules, {'markitdown': module}):
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'xlsx': legacy}):
                result = extractor.parse_file('/tmp/a.xlsx', 'xlsx')

        self.assertEqual(result.text, '表格')

    @override_settings(PARSER_BACKEND='legacy')
    def test_legacy_backend_skips_markitdown(self):
        legacy = mock.Mock(return_value=extractor.ParseResult(text='legacy'))
        module = _fake_markitdown_module()
        with mock.patch.dict(sys.modules, {'markitdown': module}):
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'docx': legacy}):
                result = extractor.parse_file('/tmp/a.docx', 'docx')

        self.assertEqual(result.text, 'legacy')

    @override_settings(PARSER_BACKEND='service', DOC_PARSE_SERVICE_URL='http://sidecar/parse')
    def test_pdf_service_result_used(self):
        response = mock.Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'markdown': '# 第一页\n正文',
            'pages': [
                {'page_no': 1, 'markdown': '第一页正文'},
                {'page_no': 2, 'text': '第二页正文'},
            ],
        }
        with mock.patch.object(extractor.requests, 'post', return_value=response) as post:
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'pdf': mock.Mock()}):
                result = extractor.parse_file(self._path('a.pdf'), 'pdf')

        self.assertEqual(result.text, '# 第一页\n正文')
        self.assertEqual([p['page_no'] for p in result.pages], [1, 2])
        post.assert_called_once()

    @override_settings(PARSER_BACKEND='service', DOC_PARSE_SERVICE_URL='http://sidecar/parse')
    def test_pdf_service_failure_falls_back_to_local(self):
        legacy = mock.Mock(return_value=extractor.ParseResult(text='本地解析'))
        with mock.patch.object(extractor.requests, 'post', side_effect=OSError('连接失败')):
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'pdf': legacy}):
                result = extractor.parse_file(self._path('a.pdf'), 'pdf')

        self.assertEqual(result.text, '本地解析')

    @override_settings(PARSER_BACKEND='service', DOC_PARSE_SERVICE_URL='')
    def test_pdf_service_unconfigured_uses_local(self):
        legacy = mock.Mock(return_value=extractor.ParseResult(text='本地解析'))
        with mock.patch.object(extractor.requests, 'post') as post:
            with mock.patch.dict(extractor._DETAILED_EXTRACTORS, {'pdf': legacy}):
                result = extractor.parse_file(self._path('a.pdf'), 'pdf')

        self.assertEqual(result.text, '本地解析')
        post.assert_not_called()
