"""快速索引（小批量冒烟）：只处理前 N 份文档。

用法：
    python scripts/index_quick.py --dry-run        # 只看统计与前 N 份是否过关
    python scripts/index_quick.py --limit 3        # 同步索引前 3 份（默认 3）

与 `index_all_docs.py` 共用同一条安全路径（`apps.knowledge.reindex_tool`），
只在 `--limit` 的默认值上不同。改造要点见 `index_all_docs.py` 的模块 docstring：

- 只处理通过 `check_indexable()` 的文档（对象在 knowledge/、
  storage_state=promoted、state 属于放行白名单）
- **不把不可索引的文档写成 `failed`**（审计报告 P-9）
- `--dry-run` 纯只读
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logging.getLogger('chromadb').setLevel(logging.WARNING)


def main(argv=None):
    parser = argparse.ArgumentParser(description='小批量索引冒烟（默认前 3 份）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只输出统计，不产生任何写入')
    parser.add_argument('--limit', type=int, default=3,
                        help='最多处理多少份文档（默认 3）')
    parser.add_argument('--force', action='store_true',
                        help='同时重建已经 indexed 的文档（不解除审核门）')
    args = parser.parse_args(argv)

    from apps.knowledge.reindex_tool import ReindexBlocked, run_reindex
    from apps.knowledge.tasks import index_document_task
    from utils.milvus import milvus_client

    def _sync_dispatch(doc):
        print(f'   → 索引 doc_id={doc.id}: {doc.title[:40]}...', end=' ', flush=True)
        try:
            index_document_task.run(None, doc.id)
        except Exception as e:                      # noqa: BLE001 — 单份失败不中断
            print(f'FAIL: {e}')
            return
        doc.refresh_from_db()
        print(f'-> {doc.status}')

    if not args.dry_run:
        print('1. 连接向量数据库...')
        milvus_client.connect()
        milvus_client.ensure_collection()
        print('2. 向量库就绪')

    try:
        run_reindex(
            dry_run=args.dry_run,
            force=args.force,
            limit=args.limit,
            dispatch=_sync_dispatch,
        )
    except ReindexBlocked as e:
        print(f'\n[BLOCKED] {e}')
        return 1

    print('3. 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
