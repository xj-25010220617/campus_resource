"""穷举类问题扩展（try_expand_enumeration）单元测试

背景：名单类查询（"15级优秀毕业生有谁"）的名单跨 13 个分块，
常规检索 top-5 槽位被同标题的多个年份文档平摊，LLM 只看到 1/13
的名单。扩展逻辑须做到：命中年份→拉对文档；未命中/歧义→不拉；
权限外文档→拒绝拉。
"""
from django.test import TestCase

from apps.knowledge.models import DocChunk, KnowledgeDoc
from utils import rag


def _make_doc(title, file_name=''):
    doc = KnowledgeDoc.objects.create(
        title=title,
        source_type='upload',
        file_name=file_name or f'{title}.pdf',
        file_type='pdf',
        status='indexed',
        visibility='public',
    )
    for i in range(3):
        DocChunk.objects.create(
            doc=doc,
            chunk_index=i,
            content=f'{title} 第{i}块名单：张三{i} 李四{i}',
            vector_id=f'doc_{doc.id}_chunk_{i}',
        )
    return doc


def _hit(doc, score):
    return {
        'doc_id': doc.id,
        'doc_title': doc.title,
        'content': '任意命中块正文',
        'score': score,
        'page': 0,
        'chunk_index': 0,
        'vector_id': f'doc_{doc.id}_chunk_0',
        'source_type': 'upload',
        'source_url': '',
        'file_name': doc.file_name,
        'oss_key': '',
    }


class TestEnumIntent(TestCase):
    def test_enumeration_keywords(self):
        for q in ('15级优秀毕业生有谁', '列出所有教师名单', '优秀教育工作者人员名单',
                  '2015届优秀毕业生都有谁', '获奖集体分别是哪些'):
            assert rag.is_enumeration_question(q) is True, q

    def test_non_enumeration(self):
        for q in ('图书馆几点开门', '罗媛媛是哪个学院的', '怎么申请奖学金'):
            assert rag.is_enumeration_question(q) is False, q

    def test_extract_years(self):
        assert rag._extract_query_years('15级优秀毕业生有谁') == ['2015']
        assert rag._extract_query_years('2015届优秀毕业生名单') == ['2015']
        assert rag._extract_query_years('优秀教师名单') == []
        # "42个教师节"这类数字不应被识别成届/级年份
        assert rag._extract_query_years('第42个教师节表彰') == []


class TestExpandEnumeration(TestCase):
    def setUp(self):
        self.ctx = {'role': 'super_admin'}
        self.doc_2015 = _make_doc('2015届优秀毕业生')
        self.doc_2013 = _make_doc('2013届优秀毕业生')
        # 复刻线上真实排序：其他年份文档分数反而更高
        self.results = [
            _hit(self.doc_2013, 0.72),
            _hit(self.doc_2015, 0.61),
        ]

    def test_year_match_expands_target_doc_full_chunks(self):
        expanded, title = rag.try_expand_enumeration(
            '15级优秀毕业生有谁', self.results, self.ctx,
        )
        assert title == '2015届优秀毕业生'
        # 目标文档 3 块全部进入，排在最前且按 chunk_index 升序
        assert [r['chunk_index'] for r in expanded[:3]] == [0, 1, 2]
        assert all(r['doc_id'] == self.doc_2015.id for r in expanded[:3])
        # 其他文档命中保留在后面，目标文档旧命中块被剔除（不重复）
        assert [r['doc_id'] for r in expanded[3:]] == [self.doc_2013.id]
        assert len(expanded) == 4

    def test_no_year_ambiguous_not_expand(self):
        # 无年份、分数接近 → 无法定位唯一目标，不扩展
        expanded, title = rag.try_expand_enumeration(
            '优秀毕业生有谁', self.results, self.ctx,
        )
        assert title is None
        assert expanded is self.results

    def test_no_year_clear_gap_expands_top1(self):
        results = [_hit(self.doc_2013, 0.72), _hit(self.doc_2015, 0.50)]
        expanded, title = rag.try_expand_enumeration(
            '优秀毕业生有谁', results, self.ctx,
        )
        assert title == '2013届优秀毕业生'
        assert len(expanded) == 4

    def test_year_not_in_results_not_expand(self):
        # 问题含年份但候选里没有对应文档 → 宁可不扩展，不能拉错年份
        expanded, title = rag.try_expand_enumeration(
            '99级优秀毕业生有谁', self.results, self.ctx,
        )
        assert title is None
        assert expanded is self.results

    def test_non_enum_question_not_expand(self):
        expanded, title = rag.try_expand_enumeration(
            '15级优秀毕业生的表彰决定内容', self.results, self.ctx,
        )
        assert title is None

    def test_unauthorized_doc_not_expand(self):
        # 普通用户语境下 private 文档越权命中 → 扩展必须拒绝
        self.doc_2015.visibility = 'private'
        self.doc_2015.uploader_id = 999999
        self.doc_2015.save(update_fields=['visibility', 'uploader_id'])
        user_ctx = {'role': 'user', 'user_id': 1}
        expanded, title = rag.try_expand_enumeration(
            '15级优秀毕业生有谁', self.results, user_ctx,
        )
        assert title is None
        assert expanded is self.results

    def test_single_chunk_doc_not_expand(self):
        doc = KnowledgeDoc.objects.create(
            title='2020届优秀毕业生', source_type='upload',
            file_name='2020届优秀毕业生.pdf', file_type='pdf',
            status='indexed', visibility='public',
        )
        DocChunk.objects.create(
            doc=doc, chunk_index=0, content='完整名单在单块内',
            vector_id=f'doc_{doc.id}_chunk_0',
        )
        expanded, title = rag.try_expand_enumeration(
            '2020届优秀毕业生有谁', [_hit(doc, 0.8)], self.ctx,
        )
        assert title is None  # 单块文档不会跨块截断，无需扩展
