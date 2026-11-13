"""父子块分块（阶段 1.2）单元测试。"""
from django.test import SimpleTestCase

from apps.knowledge.utils import chunk_document, contextual_embed_text


class ParentChildChunkTests(SimpleTestCase):
    def _text(self):
        parts = []
        for chapter in ('第一章 总则', '第二章 申请流程', '第三章 附则'):
            parts.append(chapter)
            parts.append('本章正文内容。' * 120)  # 约 840 字/章
        return '\n'.join(parts)

    def test_children_and_parents_split(self):
        chunks = chunk_document(self._text(), chunk_size=350, overlap=80, parent_size=1200)
        children = [c for c in chunks if c['chunk_level'] == 1]
        parents = [c for c in chunks if c['chunk_level'] == 0]
        self.assertGreater(len(children), 0)
        self.assertGreater(len(parents), 0)
        self.assertEqual(len(children) + len(parents), len(chunks))

    def test_child_size_limited(self):
        chunks = chunk_document(self._text(), chunk_size=350, overlap=80, parent_size=1200)
        for child in (c for c in chunks if c['chunk_level'] == 1):
            self.assertLessEqual(len(child['content']), 350)

    def test_parent_index_points_to_parent(self):
        text = self._text()
        chunks = chunk_document(text, chunk_size=350, overlap=80, parent_size=1200)
        parent_map = {c['chunk_index']: c for c in chunks if c['chunk_level'] == 0}
        for child in (c for c in chunks if c['chunk_level'] == 1):
            parent = parent_map[child['parent_index']]
            self.assertIn(child['content'][:20], parent['content'])

    def test_char_offsets_match_content(self):
        text = self._text()
        chunks = chunk_document(text, chunk_size=350, overlap=80, parent_size=1200)
        for chunk in chunks:
            self.assertEqual(
                text[chunk['char_start']:chunk['char_end']], chunk['content'],
            )

    def test_heading_path_carried(self):
        chunks = chunk_document(self._text(), chunk_size=350, overlap=80, parent_size=1200)
        paths = {c['heading_path'] for c in chunks if c['chunk_level'] == 1}
        self.assertTrue(any('第二章' in p for p in paths))

    def test_short_text_single_parent(self):
        chunks = chunk_document('简短正文。', chunk_size=350, overlap=80, parent_size=1200)
        self.assertEqual(len(chunks), 2)
        self.assertEqual([c['chunk_level'] for c in chunks], [1, 0])


class ContextualEmbedTests(SimpleTestCase):
    def test_prefix_contains_title_and_heading_path(self):
        text = contextual_embed_text('学生手册', {'content': '正文', 'heading_path': '第二章 申请'})
        self.assertEqual(text, '【学生手册 > 第二章 申请】\n正文')

    def test_no_heading_path_uses_title_only(self):
        text = contextual_embed_text('学生手册', {'content': '正文', 'heading_path': ''})
        self.assertEqual(text, '【学生手册】\n正文')

    def test_missing_metadata_keeps_content(self):
        self.assertEqual(contextual_embed_text('', {'content': '正文'}), '正文')
