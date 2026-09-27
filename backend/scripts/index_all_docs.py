"""批量索引脚本（P1-2 Phase 1-2 / 审计报告 P-1、P-9）。

用法：
    python scripts/index_all_docs.py --dry-run        # 只输出统计，不写任何东西
    python scripts/index_all_docs.py                  # 预检 + 逐份同步索引
    python scripts/index_all_docs.py --force --limit 50

流程（同步执行，不依赖 Celery worker）：
    1. `reindex_tool.collect_stats()` 输出知识库现状（含"无审核记录"数量）
    2. `reindex_tool.collect_preflight()` 逐份调用 `check_indexable()`
    3. **只对通过门禁的文档**调用 `index_document_task.run(None, doc.id)`
    4. 打印成功 / 失败 / 跳过汇总

改造要点（对应审计报告 P-9）
============================

1. **只处理通过审核门的文档**。旧版只按 `status='uploaded'` 过滤，等于把
   "未审核"当成了"待索引"。现在门禁判定唯一来源是 `check_indexable()`——
   它要求对象在 `knowledge/`、`storage_state=promoted`、`state ∈
   {approved, confirmed_unchecked}`。

2. **不再把不可索引的文档写成 `failed`**。`index_document_task` 内部在门禁
   不通过时会写 `status='failed'` + `index_error`；旧脚本正是靠这个把 95 份
   从未审核过的历史文档打成"索引失败"，制造出"系统坏了"的假象，也让这批
   文档再也回不到 `indexed`。现在这些文档在脚本层就被**跳过且不做任何修改**。

3. **`--dry-run` 纯只读**：不写库、不碰 OSS/Milvus、不删分块。

4. **下游兜底不变**：`index_document_task` 任务体内仍保留 `check_indexable()`
   校验（broker 残留消息、脚本调用、爬虫调用都绕不过去），本脚本只是
   提前把不合格的挡在外面，避免它们被误标成 `failed`。
"""
import argparse
import logging
import os
import sys
import time

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# 减少第三方库的日志噪音
logging.getLogger('chromadb').setLevel(logging.WARNING)
logging.getLogger('pymilvus').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


def ensure_vector_db():
    """确保向量数据库就绪（优先 Milvus，降级 ChromaDB）。

    只在**非 dry-run** 路径调用——dry-run 承诺不触碰 Milvus / ChromaDB。
    """
    from utils.milvus import milvus_client

    logger.info('连接向量数据库...')
    connected = milvus_client.connect()
    if connected:
        logger.info('Milvus 已连接')
    else:
        logger.warning('Milvus 不可用，使用 ChromaDB 降级')

    logger.info('确保 Collection 就绪...')
    ok = milvus_client.ensure_collection()
    if ok:
        logger.info('向量数据库就绪')
    else:
        logger.error('向量数据库初始化失败，无法继续')
        sys.exit(1)

    return milvus_client


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='批量索引知识库文档（只处理通过审核门的文档）',
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='只输出统计，不产生任何写入',
    )
    parser.add_argument(
        '--force', action='store_true',
        help='同时重建已经 indexed 的文档（不解除审核门）',
    )
    parser.add_argument(
        '--limit', type=int, default=None,
        help='最多处理多少份文档（默认全部）',
    )
    args = parser.parse_args(argv)

    from apps.knowledge.reindex_tool import ReindexBlocked, run_reindex
    from apps.knowledge.tasks import index_document_task

    counters = {'ok': 0, 'fail': 0}

    def _sync_dispatch(doc):
        """逐份同步索引；失败只记数，绝不中断整批。"""
        start = time.time()
        try:
            # 同步调用 index_document_task（.apply 同步执行；.run(None, ...) 在新版
            # Celery 会因 run 已绑定 task 实例而多传一个位置参数，见 reindex_all._reindex_doc）
            if hasattr(index_document_task, 'apply'):
                index_document_task.apply(args=(doc.id,), throw=True)
            else:
                index_document_task.delay(doc.id)
        except Exception as e:                      # noqa: BLE001 — 单份失败不影响其他
            counters['fail'] += 1
            print(f'  ✗ doc_id={doc.id} {type(e).__name__}: {e}')
            return
        doc.refresh_from_db()
        if doc.status == 'indexed':
            counters['ok'] += 1
            print(f'  ✓ doc_id={doc.id} {time.time() - start:.1f}s '
                  f'(chunks={doc.chunk_count})')
        else:
            counters['fail'] += 1
            print(f'  ✗ doc_id={doc.id} status={doc.status} '
                  f'err={doc.index_error[:120]}')

    if not args.dry_run:
        ensure_vector_db()

    try:
        result = run_reindex(
            dry_run=args.dry_run,
            force=args.force,
            limit=args.limit,
            dispatch=_sync_dispatch,
        )
    except ReindexBlocked as e:
        print(f'\n[BLOCKED] {e}')
        return 1

    if not args.dry_run:
        print(f'\n成功 {counters["ok"]}  失败 {counters["fail"]}  '
              f'跳过 {result["skipped"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
