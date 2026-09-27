"""安全隔离 P0/P1 修复 —— 定向回归测试（2026-09-27）。

覆盖本轮四个合并阻塞项，一项一组：

    P0-1  knowledge 迁移图线性化（0007 → 0008_utf8mb4_charset →
          0009_contentmoderation → 0010_uploadintent）：单叶、无冲突、
          字符集迁移必须排在建表之前、安全迁移不得含 RunSQL/RunPython。
    P0-2  爬虫两条写入路径（网页正文 / 外部附件）都必须经
          `upload_service.ingest_internal_document()` 进入隔离区，
          不得自己拼 `knowledge/` key、不得自己投递索引任务；
          `download_files=True` 仍然可用。
    P1-1  图片检索的准入由读取侧审核门决定（approved / confirmed_unchecked
          可检索，pending / scanning / rejected / unavailable / error 不可），
          且不再因为 `status != 'indexed'` 把合法图片整体过滤掉。
    P1-4  UploadCallbackSerializer 的 `intent_id` 保持必填。

测试原则：不连接任何真实外部依赖（OSS / Celery / Milvus 全部替身），
不访问网络。被替身化的是**外部服务**，不是**安全判定本身**。
"""
import ast
import hashlib
import os
import sys
import types
import unittest
from unittest.mock import patch

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import College, User

from .models import ContentModeration, KnowledgeDoc
from .moderation import (
    MODERATION_STATE_APPROVED,
    MODERATION_STATE_CONFIRMED_UNCHECKED,
    MODERATION_STATE_ERROR,
    MODERATION_STATE_PENDING,
    MODERATION_STATE_REJECTED,
    MODERATION_STATE_SCANNING,
    MODERATION_STATE_UNAVAILABLE,
    STORAGE_STATE_PROMOTED,
    STORAGE_STATE_QUARANTINE,
)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CRAWLER_PATH = os.path.join(BACKEND_DIR, 'utils', 'crawler.py')


def _ensure_crawler_importable():
    """让 `utils.crawler` 在缺 bs4 的测试环境里也能导入。

    本次被测的是爬虫的**写库路径**（正文/附件 → 隔离区 → 审核编排），
    不涉及 HTML 解析，而 BeautifulSoup 只在解析函数里才真正用到。这里注入
    最小替身，任何真实解析调用都会立刻报错，不会被静默吞掉——**只影响测试
    进程**，不改动 requirements，也不改动生产代码。
    """
    if 'bs4' in sys.modules:
        return
    try:
        import bs4  # noqa: F401
        return
    except ImportError:
        pass

    shim = types.ModuleType('bs4')

    class _Bs4NotAvailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                '测试环境未安装 bs4：HTML 解析不在本用例范围内',
            )

    shim.BeautifulSoup = _Bs4NotAvailable
    sys.modules['bs4'] = shim


# ===========================================================================
# P0-1：迁移图
# ===========================================================================

class MigrationGraphLinearizationTests(SimpleTestCase):
    """合并 main 后 knowledge 必须仍是**单叶线性图**（否则 migrate 直接 CommandError）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # connection=None：只读磁盘上的迁移文件，不连库、不写库
        cls.loader = MigrationLoader(None, ignore_no_migrations=True)

    def test_no_conflicting_migrations_in_any_app(self):
        self.assertEqual(
            self.loader.detect_conflicts(), {},
            '存在多叶（冲突）迁移：migrate 会在访问数据库之前直接失败',
        )

    def test_every_app_has_exactly_one_leaf(self):
        apps = sorted({node[0] for node in self.loader.graph.nodes})
        multi = {
            app: self.loader.graph.leaf_nodes(app)
            for app in apps
            if len(self.loader.graph.leaf_nodes(app)) != 1
        }
        self.assertEqual(multi, {})

    def test_knowledge_keeps_a_single_leaf(self):
        self.assertEqual(
            self.loader.graph.leaf_nodes('knowledge'),
            [('knowledge', '0010_uploadintent_chunkeduploadsession_intent_and_more')],
        )

    def test_charset_migration_precedes_the_three_security_tables(self):
        """字符集必须排在建表之前——反过来新表会落在 utf8 下，中文字段被截断。"""
        chain = self._knowledge_chain()
        self.assertEqual(
            chain,
            [
                '0007_knowledgedoc_index_diagnostics',
                '0008_utf8mb4_charset',
                '0009_contentmoderation_moderationattempt_and_more',
                '0010_uploadintent_chunkeduploadsession_intent_and_more',
            ],
        )

    def test_security_migrations_add_no_data_operations(self):
        """0009/0010 只建表/加字段/建索引，不得含 RunSQL / RunPython 数据迁移。"""
        for name in (
            '0009_contentmoderation_moderationattempt_and_more',
            '0010_uploadintent_chunkeduploadsession_intent_and_more',
        ):
            ops = [
                type(op).__name__
                for op in self.loader.disk_migrations[('knowledge', name)].operations
            ]
            self.assertNotIn('RunSQL', ops, f'{name} 不应含 RunSQL')
            self.assertNotIn('RunPython', ops, f'{name} 不应含 RunPython')

    def test_charset_semantics_are_preserved(self):
        """0008 仍然把 database 与四张既有表的字符集转成 utf8mb4。"""
        migration = self.loader.disk_migrations[('knowledge', '0008_utf8mb4_charset')]
        sql = ' '.join(
            str(op.sql) for op in migration.operations if type(op).__name__ == 'RunSQL'
        )
        self.assertIn('CHARACTER SET utf8mb4', sql)
        self.assertIn('COLLATE utf8mb4_unicode_ci', sql)
        for table in ('knowledge_doc', 'keyword', 'doc_chunk', 'chunked_upload_session'):
            self.assertIn(table, sql, f'{table} 的字符集转换不能丢')

    def test_no_broken_dependencies(self):
        missing = [
            (key, dep)
            for key, node in self.loader.graph.node_map.items()
            for dep in node.parents
            if dep not in self.loader.graph.nodes
        ]
        self.assertEqual(missing, [])

    def _knowledge_chain(self):
        """从 0007 起沿唯一同 app 依赖走到 leaf 的名称序列。"""
        chain = []
        cur = ('knowledge', '0007_knowledgedoc_index_diagnostics')
        while cur is not None:
            chain.append(cur[1])
            children = [
                key for key, node in self.loader.graph.node_map.items()
                if cur in node.parents and key[0] == 'knowledge'
            ]
            cur = children[0] if len(children) == 1 else None
        return chain


# ===========================================================================
# P0-2：内部写入统一入口
# ===========================================================================

class _FakeBucket:
    """OSS 桶替身：记录 put_object 的 (key, size, headers)，不做任何网络调用。"""

    def __init__(self):
        self.puts = []

    def put_object(self, key, data, headers=None):
        self.puts.append((key, len(data), headers))


class _FakeOssClient:
    bucket_name = 'campus-resourse'
    endpoint = 'oss-cn-chengdu.aliyuncs.com'

    def __init__(self):
        self.bucket = _FakeBucket()


class _DispatcherRecorder:
    """`dispatch_async` 替身：只记录被派发的任务，不执行任务体。"""

    def __init__(self):
        self.calls = []

    def __call__(self, task, *args, **kwargs):
        self.calls.append((getattr(task, 'name', task), args, kwargs))


class InternalIngestRoutingTests(TestCase):
    """爬虫的两种对象类型都必须经统一入口进入隔离区。"""

    def setUp(self):
        _ensure_crawler_importable()
        self.college = College.objects.create(name='计算机与软件工程学院', code='CS')
        self.admin = User.objects.create_user(
            username='p02_admin', password='testpass123',
            role='super_admin', college=self.college,
        )
        self.oss = _FakeOssClient()

    # ---- 1. 网页正文（upload_text 路径） ----
    def test_page_text_lands_in_quarantine_and_enters_moderation(self):
        from utils import crawler

        dispatcher = _DispatcherRecorder()
        with patch('apps.knowledge.upload_service.oss_client', self.oss), \
                patch('utils.celery_dispatcher.dispatch_async', dispatcher), \
                self.captureOnCommitCallbacks(execute=True):
            doc_id = crawler.save_page_as_knowledge_doc(
                {'title': '跳绳项目工作报告', 'content': '正文内容' * 40,
                 'url': 'https://www.nsu.edu.cn/HTML/news/article_1.html'},
                uploader_id=self.admin.id,
            )

        doc = KnowledgeDoc.objects.get(id=doc_id)
        self.assertTrue(doc.oss_key.startswith('quarantine/'), doc.oss_key)
        self.assertNotIn('knowledge/', doc.oss_key)
        self.assertEqual(doc.status, 'uploaded')
        self.assertEqual(doc.source_type, 'website')
        self.assertEqual(doc.source_url, 'https://www.nsu.edu.cn/HTML/news/article_1.html')
        self.assertTrue(doc.file_name.startswith('crawl_'))
        self.assertTrue(doc.file_name.endswith('.txt'))
        self.assertEqual(doc.uploader_id, self.admin.id)
        # 对象写在文档记录指向的隔离区 key 上（同一个 key，不是另拼的正式区 key）
        self.assertEqual([k for k, _, _ in self.oss.bucket.puts], [doc.oss_key])
        # 入口不得自己造审核记录：审核由 moderate_and_promote_task 完成
        self.assertFalse(ContentModeration.objects.filter(doc=doc).exists())
        # 但必须已经进入统一审核编排
        self.assertEqual(len(dispatcher.calls), 1)
        self.assertEqual(dispatcher.calls[0][1], (doc.id,))

    # ---- 2. 外部附件（upload_bytes 路径） ----
    def test_downloaded_attachment_lands_in_quarantine_and_enters_moderation(self):
        from utils import crawler

        content = b'%PDF-1.4 fake attachment payload'
        file_data = {
            'url': 'https://www.nsu.edu.cn/files/2026/09/招生简章.pdf',
            'file_name': '招生简章.pdf',
            'ext': 'pdf',
            'content': content,
            'size': len(content),
            'md5': hashlib.md5(content).hexdigest(),
            'title_hint': '点击下载',        # 无意义锚文本 → 退化为文件名主干
            'source_page': 'https://www.nsu.edu.cn/HTML/news/article_1.html',
        }

        dispatcher = _DispatcherRecorder()
        with patch('apps.knowledge.upload_service.oss_client', self.oss), \
                patch('utils.celery_dispatcher.dispatch_async', dispatcher), \
                self.captureOnCommitCallbacks(execute=True):
            doc_id = crawler.save_file_as_knowledge_doc(
                file_data, uploader_id=self.admin.id,
            )

        doc = KnowledgeDoc.objects.get(id=doc_id)
        self.assertTrue(doc.oss_key.startswith('quarantine/'), doc.oss_key)
        self.assertNotIn('knowledge/', doc.oss_key)
        self.assertTrue(doc.oss_key.endswith('.pdf'))
        self.assertEqual(doc.status, 'uploaded')
        self.assertEqual(doc.source_type, 'website')
        self.assertEqual(doc.file_name, '招生简章.pdf')
        self.assertEqual(doc.file_size, len(content))
        self.assertEqual(doc.title, '招生简章')
        self.assertEqual([k for k, _, _ in self.oss.bucket.puts], [doc.oss_key])
        self.assertFalse(ContentModeration.objects.filter(doc=doc).exists())
        self.assertEqual(len(dispatcher.calls), 1)
        self.assertEqual(dispatcher.calls[0][1], (doc.id,))

    def test_size_mismatch_is_refused_and_leaves_no_doc(self):
        """字节数与声明不符 → 意图拒绝完成，不落下任何文档记录。"""
        from apps.knowledge import upload_service

        content = b'%PDF-1.4'
        with patch('apps.knowledge.upload_service.oss_client', self.oss):
            intent = upload_service.begin_upload(
                self.admin, 'x.pdf', content_type='application/pdf',
                expected_size=len(content),
            )
            with self.assertRaises(upload_service.UploadIntentError):
                # 实际字节多于声明值
                upload_service.put_quarantine_object(intent, content + b'extra')

        self.assertFalse(KnowledgeDoc.objects.exists())

    # ---- 3. 静态守卫：爬虫不再有正式区写入能力 ----
    def test_crawler_sources_contain_no_official_key_literals(self):
        src = _read(CRAWLER_PATH)
        tree = ast.parse(src)
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        offenders = [s for s in literals if s.startswith('knowledge/')]
        self.assertEqual(offenders, [], f'爬虫里仍有正式区 key 字面量：{offenders}')

    def test_crawler_sources_never_call_direct_oss_or_index_helpers(self):
        src = _read(CRAWLER_PATH)
        for forbidden in ('upload_text(', 'upload_bytes(', 'index_document_task',
                          'ContentModeration', 'moderate_document'):
            self.assertNotIn(forbidden, src, f'爬虫不应再出现 {forbidden}')

    def test_both_crawler_savers_call_the_internal_service_entry(self):
        tree = ast.parse(_read(CRAWLER_PATH))
        for func_name in ('save_page_as_knowledge_doc', 'save_file_as_knowledge_doc'):
            func = next(
                node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == func_name
            )
            called = {
                node.func.attr for node in ast.walk(func)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            self.assertIn(
                'ingest_internal_document', called,
                f'{func_name} 必须经 upload_service.ingest_internal_document() 写库',
            )

    # ---- 4. download_files=True 仍然可用 ----
    def test_download_files_true_still_downloads_and_saves_attachments(self):
        from utils import crawler

        content = b'%PDF-1.4 attachment'
        file_data = {
            'url': 'https://www.nsu.edu.cn/files/a.pdf',
            'file_name': 'a.pdf', 'ext': 'pdf', 'content': content,
            'size': len(content), 'md5': hashlib.md5(content).hexdigest(),
            'title_hint': '招生简章', 'source_page': '',
        }

        class _Spider:
            max_files = crawler.MAX_FILES_PER_RUN
            file_links = [{'url': file_data['url'], 'text': '', 'source_page': ''}]

            def __init__(self, *args, **kwargs):
                pass

            def start_crawl(self, max_pages=100):
                return []

            def download_file(self, link):
                return file_data

        dispatcher = _DispatcherRecorder()
        with patch.object(crawler, 'NeusoftSpider', _Spider), \
                patch('apps.knowledge.upload_service.oss_client', self.oss), \
                patch('utils.celery_dispatcher.dispatch_async', dispatcher), \
                self.captureOnCommitCallbacks(execute=True):
            saved = crawler.crawl_and_index(max_pages=1, download_files=True)

        self.assertEqual(saved, 1)
        doc = KnowledgeDoc.objects.get()
        self.assertTrue(doc.oss_key.startswith('quarantine/'))

    def test_download_files_false_skips_attachments_entirely(self):
        from utils import crawler

        class _Spider:
            max_files = crawler.MAX_FILES_PER_RUN
            file_links = [{'url': 'https://www.nsu.edu.cn/files/a.pdf',
                           'text': '', 'source_page': ''}]

            def __init__(self, *args, **kwargs):
                pass

            def start_crawl(self, max_pages=100):
                return []

            def download_file(self, link):   # pragma: no cover - 不应被调用
                raise AssertionError('download_files=False 时不应下载附件')

        with patch.object(crawler, 'NeusoftSpider', _Spider):
            saved = crawler.crawl_and_index(max_pages=1, download_files=False)

        self.assertEqual(saved, 0)
        self.assertFalse(KnowledgeDoc.objects.exists())


# ===========================================================================
# P1-1：图片检索的读取侧审核门
# ===========================================================================

class ImageRetrievalGateTests(TestCase):
    """图片准入 = 审核门（state 白名单 + storage_state=promoted），不是 status。"""

    def setUp(self):
        self.college = College.objects.create(name='计算机与软件工程学院', code='CS')
        self.admin = User.objects.create_user(
            username='p11_admin', password='testpass123',
            role='super_admin', college=self.college,
        )
        self.ctx = {'role': 'super_admin', 'user_id': self.admin.id}

    def _image(self, title, *, state, storage_state=STORAGE_STATE_PROMOTED,
               status='uploaded', file_type='image'):
        doc = KnowledgeDoc.objects.create(
            title=title, description=title, source_type='upload',
            file_name=f'{title}.png', file_type=file_type, uploader=self.admin,
            oss_key='quarantine/school/0/2026-09/img.png'
                    if storage_state != STORAGE_STATE_PROMOTED
                    else 'knowledge/school/0/2026-09/img.png',
            status=status,
        )
        ContentModeration.objects.create(
            doc=doc, state=state, storage_state=storage_state,
            quarantine_key='quarantine/school/0/2026-09/img.png',
        )
        return doc

    def _retrieved_ids(self, question='篮球场'):
        from utils.rag import retrieve_images
        return {r['doc_id'] for r in retrieve_images(question, user_context=self.ctx)}

    def test_approved_and_promoted_image_is_retrievable(self):
        doc = self._image('篮球场全景', state=MODERATION_STATE_APPROVED)
        self.assertIn(doc.id, self._retrieved_ids())

    def test_confirmed_unchecked_and_promoted_image_is_retrievable(self):
        doc = self._image('篮球场俯视图', state=MODERATION_STATE_CONFIRMED_UNCHECKED)
        self.assertIn(doc.id, self._retrieved_ids())

    def test_pending_image_is_not_retrievable(self):
        doc = self._image('篮球场待审图', state=MODERATION_STATE_PENDING)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_scanning_image_is_not_retrievable(self):
        doc = self._image('篮球场审核中图', state=MODERATION_STATE_SCANNING)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_rejected_image_is_not_retrievable(self):
        doc = self._image('篮球场违规图', state=MODERATION_STATE_REJECTED)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_unavailable_image_is_not_retrievable(self):
        doc = self._image('篮球场无法判定图', state=MODERATION_STATE_UNAVAILABLE)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_error_image_is_not_retrievable(self):
        doc = self._image('篮球场审核异常图', state=MODERATION_STATE_ERROR)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_approved_but_not_promoted_image_is_not_retrievable(self):
        doc = self._image('篮球场未提升图', state=MODERATION_STATE_APPROVED,
                          storage_state=STORAGE_STATE_QUARANTINE)
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_image_without_moderation_record_is_not_retrievable(self):
        doc = KnowledgeDoc.objects.create(
            title='篮球场历史图', description='篮球场历史图', source_type='upload',
            file_name='old.png', file_type='image', uploader=self.admin,
            oss_key='knowledge/college/1/2026-01/old.png', status='indexed',
        )
        self.assertNotIn(doc.id, self._retrieved_ids())

    def test_approved_image_is_not_filtered_out_by_non_indexed_status(self):
        """回归点：曾经按 status='indexed' 过滤 → 合法图片全部检索不到。"""
        uploaded = self._image('篮球场施工中', state=MODERATION_STATE_APPROVED,
                               status='uploaded')
        failed = self._image('篮球场夜景', state=MODERATION_STATE_APPROVED,
                             status='failed')
        indexed = self._image('篮球场看台', state=MODERATION_STATE_APPROVED,
                              status='indexed')
        ids = self._retrieved_ids()
        self.assertIn(uploaded.id, ids)
        self.assertIn(failed.id, ids)
        self.assertIn(indexed.id, ids)

    def test_historical_image_extensions_are_still_covered(self):
        from apps.knowledge.serializers import IMAGE_FILE_TYPES
        ext = sorted(IMAGE_FILE_TYPES)[0]
        doc = self._image('篮球场历史扩展名', state=MODERATION_STATE_APPROVED,
                          file_type=ext)
        self.assertIn(doc.id, self._retrieved_ids())


# ===========================================================================
# P1-4：回调的 intent_id 契约
# ===========================================================================

class UploadCallbackIntentContractTests(SimpleTestCase):
    """`intent_id` 必须保持必填（缺了就没有归属依据）。"""

    def _payload(self, **kw):
        base = dict(
            oss_key='quarantine/school/0/2026-09/abcdef123456.pdf',
            file_name='软件工程导论.pdf',
            file_size=2048000,
            file_type='pdf',
            md5='d41d8cd98f00b204e9800998ecf8427e',
        )
        base.update(kw)
        return base

    def test_intent_id_field_is_required(self):
        from .serializers import UploadCallbackSerializer
        self.assertTrue(UploadCallbackSerializer().fields['intent_id'].required)

    def test_missing_intent_id_is_a_400_payload_error(self):
        from .serializers import UploadCallbackSerializer
        serializer = UploadCallbackSerializer(data=self._payload())
        self.assertFalse(serializer.is_valid())
        self.assertIn('intent_id', serializer.errors)

    def test_payload_with_intent_id_is_accepted(self):
        from .serializers import UploadCallbackSerializer
        serializer = UploadCallbackSerializer(data=self._payload(intent_id=37))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['intent_id'], 37)

    def test_intent_id_must_be_an_integer(self):
        from .serializers import UploadCallbackSerializer
        serializer = UploadCallbackSerializer(
            data=self._payload(intent_id='not-an-int'),
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('intent_id', serializer.errors)


def _read(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
