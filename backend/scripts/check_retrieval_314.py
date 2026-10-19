"""服务器端诊断：模拟用户查询，看 2015届优秀毕业生 的检索召回情况"""
import os
import sys

sys.path.insert(0, '/www/wwwroot/campus_resource/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from utils import rag

QUESTIONS = [
    '15级优秀毕业生',
    '15级优秀毕业生有哪些人',
    '列出15级优秀毕业生名单',
]

for q in QUESTIONS:
    print('=' * 70)
    print('问题:', q)
    results = rag.retrieve(
        question=q,
        user_context={'role': 'super_admin'},
        parsed_filters={},
    )
    print('召回条数:', len(results))
    total_chars = 0
    doc314_chunks = 0
    for r in results:
        is_314 = r['doc_id'] == 314
        if is_314:
            doc314_chunks += 1
        total_chars += len(r.get('content', ''))
        print(
            f"  doc={r['doc_id']} chunk_index={r.get('chunk_index')} "
            f"score={r['score']:.3f} len={len(r.get('content', ''))} "
            f"title={r['doc_title'][:24]}"
            + (' <== 314名单块' if is_314 else '')
        )
    print(f'进入 LLM 上下文总字符: {total_chars} / 8000')
    print(f'doc 314 名单块进入上下文: {doc314_chunks} / 13')
