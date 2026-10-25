"""标签检索通道（第 5 路召回 + 标签穷举）单元测试

背景：标签是人工打标的全平台共享词表，不带检索权重。接入智能问答后：
- 隐式通道：问题文本命中已打标签时，标签下文档作为第 5 路召回软增强；
- 穷举通道：命中恰好 1 个标签且含列举意图时，返回该标签下全部文件清单。
权限约束：两条通道都必须经过 authorized_knowledge_docs + status='indexed'，
不得成为越权读取通道。
"""
from django.test import TestCase

from apps.knowledge.models import DocChunk, DocTag, KnowledgeDoc, Tag
from utils import rag


def _make_doc(title, status='indexed', visibility='public', uploader_id=None):
    doc = KnowledgeDoc.objects.create(
        title=title,
        source_type='upload',
        file_name=f'{title}.pdf',
        file_type='pdf',
        status=status,
        visibility=visibility,
        uploader_id=uploader_id,
    )
    DocChunk.objects.create(
        doc=doc, chunk_index=0,
        content=f'{title} 正文内容',
        vector_id=f'doc_{doc.id}_chunk_0',
    )
    return doc


def _tag_doc(doc, tag):
    DocTag.objects.create(doc=doc, tag=tag)


class TagCacheTestMixin:
    """每个用例前清空词表缓存，避免 60s TTL 缓存跨用例污染。"""

    def setUp(self):
        super().setUp()
        rag.invalidate_tag_cache()
        self.addCleanup(rag.invalidate_tag_cache)


class TestMatchTags(TagCacheTestMixin, TestCase):
    def test_substring_match(self):
        t = Tag.objects.create(name='奖学金评定')
        matched = rag._match_tags('奖学金评定需要什么材料')
        assert [m['tag_id'] for m in matched] == [t.id]

    def test_nested_shorter_tag_dropped(self):
        # 同时命中"奖学金"与"奖学金评定"时，被包含的短标签应剔除，降低歧义
        t_short = Tag.objects.create(name='奖学金')
        t_long = Tag.objects.create(name='奖学金评定')
        matched = rag._match_tags('奖学金评定的流程')
        assert [m['tag_id'] for m in matched] == [t_long.id]

    def test_single_char_tag_ignored(self):
        Tag.objects.create(name='书')
        assert rag._match_tags('图书馆几点开门') == []

    def test_no_match(self):
        Tag.objects.create(name='奖学金评定')
        assert rag._match_tags('图书馆几点开门') == []

    def test_cache_invalidates_on_new_tag(self):
        # 先查询填充缓存，再建新标签；不失效时查不到，失效后立即可见
        assert rag._match_tags('关于社团活动的问题') == []
        t = Tag.objects.create(name='社团活动')
        assert rag._match_tags('社团活动场地怎么申请') == []  # 缓存未过期
        rag.invalidate_tag_cache()
        matched = rag._match_tags('社团活动场地怎么申请')
        assert [m['tag_id'] for m in matched] == [t.id]


class TestTagTableSearch(TagCacheTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.ctx = {'role': 'super_admin'}
        self.tag = Tag.objects.create(name='奖学金评定')
        self.doc = _make_doc('奖学金评定办法')
        _tag_doc(self.doc, self.tag)

    def test_tagged_doc_recalled(self):
        hits = rag._tag_table_search([{'tag_id': self.tag.id, 'name': self.tag.name}], self.ctx)
        assert [h['doc_id'] for h in hits] == [self.doc.id]
        assert hits[0]['score'] == 0.6

    def test_unindexed_doc_excluded(self):
        pending = _make_doc('奖学金评定细则', status='pending')
        _tag_doc(pending, self.tag)
        hits = rag._tag_table_search([{'tag_id': self.tag.id, 'name': self.tag.name}], self.ctx)
        assert self.doc.id in [h['doc_id'] for h in hits]
        assert pending.id not in [h['doc_id'] for h in hits]

    def test_unauthorized_private_doc_excluded(self):
        priv = _make_doc('内部评定名单', visibility='private', uploader_id=999999)
        _tag_doc(priv, self.tag)
        hits = rag._tag_table_search(
            [{'tag_id': self.tag.id, 'name': self.tag.name}],
            {'role': 'user', 'user_id': 1},
        )
        assert priv.id not in [h['doc_id'] for h in hits]

    def test_respects_candidate_ids_scope(self):
        hits = rag._tag_table_search(
            [{'tag_id': self.tag.id, 'name': self.tag.name}],
            self.ctx, doc_ids=[-1],
        )
        assert hits == []


class TestTagListing(TagCacheTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.ctx = {'role': 'super_admin'}
        self.tag = Tag.objects.create(name='奖学金评定')
        self.doc_a = _make_doc('奖学金评定办法')
        self.doc_b = _make_doc('奖学金申请表')
        _tag_doc(self.doc_a, self.tag)
        _tag_doc(self.doc_b, self.tag)

    def test_listing_triggered_by_all_files_intent(self):
        results, name = rag.try_expand_tag_listing('奖学金评定相关的所有文件有哪些', self.ctx)
        assert name == '奖学金评定'
        assert {r['doc_id'] for r in results} == {self.doc_a.id, self.doc_b.id}
        # 每文档一条，分数高于引用阈值，保证进入 references
        assert all(r['chunk_index'] is None for r in results)
        assert all(r['score'] >= rag.RAG_REFERENCE_THRESHOLD for r in results)

    def test_no_intent_not_triggered(self):
        results, name = rag.try_expand_tag_listing('奖学金评定需要什么材料', self.ctx)
        assert (results, name) == (None, None)

    def test_ambiguous_two_tags_not_triggered(self):
        tag2 = Tag.objects.create(name='申请表')
        _tag_doc(self.doc_b, tag2)
        # 问题同时命中"奖学金评定"与"申请表"两个标签 → 歧义不猜
        results, name = rag.try_expand_tag_listing('奖学金评定和申请表的文件都有哪些', self.ctx)
        assert (results, name) == (None, None)

    def test_zero_matched_tags_not_triggered(self):
        results, name = rag.try_expand_tag_listing('图书馆的所有文件列出来', self.ctx)
        assert (results, name) == (None, None)

    def test_unauthorized_docs_excluded_from_listing(self):
        priv = _make_doc('内部评定名单', visibility='private', uploader_id=999999)
        _tag_doc(priv, self.tag)
        results, name = rag.try_expand_tag_listing(
            '奖学金评定所有文件', {'role': 'user', 'user_id': 1},
        )
        assert name == '奖学金评定'
        assert priv.id not in [r['doc_id'] for r in results]
        assert {r['doc_id'] for r in results} == {self.doc_a.id, self.doc_b.id}

    def test_max_docs_cap(self):
        for i in range(5):
            _tag_doc(_make_doc(f'补充材料{i}'), self.tag)
        results, _ = rag.try_expand_tag_listing('奖学金评定所有文件', self.ctx, max_docs=3)
        assert len(results) == 3

    def test_is_tag_listing_question(self):
        for q in ('奖学金评定有哪些文件', '标签下文档列表', '社团活动都有什么资料'):
            assert rag.is_tag_listing_question(q) is True, q
        assert rag.is_tag_listing_question('奖学金评定需要什么材料') is False
