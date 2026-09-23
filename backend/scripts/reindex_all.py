"""
全量重索引脚本：重建 Milvus Collection 并对所有知识文档重新索引。

用途：向量库损坏/维度迁移/Embedding 模型切换后一键重建。
会清空旧向量（含孤儿向量），再逐文档重新解析 → 分块 → Embedding → 入库。

用法：
    cd backend && python scripts/reindex_all.py            # 全量重建
    cd backend && python scripts/reindex_all.py --if-empty # 仅当需要时重建（CI/CD）

流程：
    1. 连接 Milvus（失败即退出）
    2. drop 旧 collection 并重建空 collection（清空孤儿向量）
    3. 遍历所有 KnowledgeDoc（排除正在上传的会话态），同步调用
       index_document_task 重新索引
    4. 打印进度与结果汇总

中断自愈（2026-09-23）：
    重灌循环开始前创建标记文件（/tmp/campus_reindex_incomplete），
    仅在"正常跑完"时删除；被打断（超时/kill/异常）则标记留存。
    下次 --if-empty 看到标记会强制全量重灌，避免"部分数据
    （entities>0）让 --if-empty 永久跳过"的陷阱。
"""
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.db.models import Q

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# 减少第三方库日志噪音
logging.getLogger('pymilvus').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

# 中断标记：重灌循环开始前创建、正常完成后删除。
# Linux 服务器上 tempfile.gettempdir() 即 /tmp，本地 Windows 开发环境也能跑通。
REINDEX_MARKER = os.path.join(tempfile.gettempdir(), 'campus_reindex_incomplete')


def _reindex_doc(doc_id: int):
    """同步调用 index_document_task，兼容真实 Celery 与本地降级两种模式。

    - 真实 Celery：用 .apply(args=..., throw=True) 同步执行，bind=True 的 self
      由 Celery 正确注入，任务内异常向上抛（重灌循环据此计 FAIL）。
      注意不能用旧写法 .run(None, doc_id)：新版 Celery 的 task.run 已绑定
      task 实例，再传 None 会多出一个位置参数，报
      TypeError: index_document_task() takes 2 positional arguments but 3 were given
      （2026-09-23 重灌全灭的直接原因）。
    - 本地降级（未装 celery）：task 无 .apply，走 .delay 同步兼容层
      （shared_task_compat 会自动补 None 占位 self）。
    """
    from apps.knowledge.tasks import index_document_task

    if hasattr(index_document_task, 'apply'):
        return index_document_task.apply(args=(doc_id,), throw=True)
    return index_document_task.delay(doc_id)


def rebuild_collection() -> bool:
    """drop 并重建 collection，清空所有旧向量（含孤儿向量）。"""
    from pymilvus import utility

    from utils.milvus import milvus_client

    milvus_client.connect()
    if not milvus_client.connected:
        logger.error('Milvus 连接失败，请确认 MILVUS_URI 路径可写及 milvus-lite 已安装')
        return False

    name = milvus_client.COLLECTION_NAME
    if utility.has_collection(name):
        logger.info('发现旧 collection "%s"，准备删除...', name)
        try:
            utility.drop_collection(name)
            logger.info('已删除旧 collection')
        except Exception as e:
            logger.error('删除旧 collection 失败: %s', e)
            return False

    ok = milvus_client.ensure_collection()
    if not ok:
        logger.error('新建 collection 失败')
        return False
    logger.info('新 collection 就绪（dim=%s）', milvus_client.VECTOR_DIM)
    return True


def milvus_is_empty() -> bool:
    """判断 Milvus 是否需要重建：collection 不存在、向量数为 0，或维度与当前
    Embedding 配置不兼容（旧后端创建的集合，插入会报 expected dim X, got Y）。

    CI/CD 用 --if-empty 调用时据此决定是否重建；判断过程异常时返回 True
    （按空处理），交由后续 rebuild_collection 重连并给出最终结论（连不上会非零退出）。
    """
    from pymilvus import Collection, utility

    from utils.milvus import milvus_client

    try:
        milvus_client.connect()
        if not milvus_client.connected:
            return True
        name = milvus_client.COLLECTION_NAME
        if not utility.has_collection(name):
            return True
        if Collection(name).num_entities == 0:
            return True
        existing_dim = milvus_client.get_collection_dim()
        if existing_dim is not None and existing_dim != milvus_client.VECTOR_DIM:
            logger.warning(
                'Milvus 集合维度 %d 与当前 Embedding 配置 %d 维不一致，需重建',
                existing_dim, milvus_client.VECTOR_DIM,
            )
            return True
        return False
    except Exception as e:
        logger.warning('判断 Milvus 是否为空失败，按空处理: %s', e)
        return True


def reindex_all():
    from apps.knowledge.models import KnowledgeDoc

    # 状态分布
    from django.db.models import Count
    dist = dict(
        KnowledgeDoc.objects.values_list('status').annotate(c=Count('id'))
    )
    total = sum(dist.values())
    print(f'\n{"="*55}')
    print('  全量重索引')
    print(f'{"="*55}')
    print(f'  总文档: {total}')
    for status, c in sorted(dist.items()):
        print(f'    {status}: {c}')
    print(f'{"="*55}\n')

    if total == 0:
        print('没有文档，退出。')
        return

    # 排除正在上传的会话态（若存在）；其余一律重索引（含 failed/已索引）
    docs = KnowledgeDoc.objects.filter(~Q(status='uploading')).order_by('id')
    n = docs.count()

    success_count = 0
    fail_count = 0
    t0 = time.time()

    for i, doc in enumerate(docs, 1):
        print(f'[{i}/{n}] 重索引 #{doc.id}: "{doc.title[:50]}"...', end=' ', flush=True)
        start = time.time()
        try:
            _reindex_doc(doc.id)
            doc.refresh_from_db()
            elapsed = time.time() - start
            if doc.status == 'indexed':
                print(f'OK {elapsed:.1f}s (chunks={doc.chunk_count})')
                success_count += 1
            else:
                print(f'FAIL {elapsed:.1f}s (status={doc.status})')
                fail_count += 1
        except Exception as e:
            elapsed = time.time() - start
            print(f'FAIL {elapsed:.1f}s — {type(e).__name__}: {e}')
            fail_count += 1

    total_elapsed = time.time() - t0
    print(f'\n{"="*55}')
    print(f'  重索引完成!')
    print(f'  耗时: {total_elapsed:.1f}s')
    print(f'  成功: {success_count}')
    print(f'  失败: {fail_count}')
    print(f'{"="*55}')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='全量重索引 Milvus 知识库')
    parser.add_argument(
        '--if-empty', action='store_true',
        help='仅当 Milvus 为空、维度不兼容、或上次重灌留有中断标记时才重建'
             '（供 CI/CD 部署安全调用，已有兼容数据时自动跳过，不重复灌库）',
    )
    args = parser.parse_args()

    # CI/CD 守卫（--if-empty）：满足任一条件才重建——
    #   1. Milvus 为空 / 维度与当前 Embedding 配置不兼容（milvus_is_empty）
    #   2. 上次重灌被中断，标记文件还在（被超时/kill 打断的部分重灌必须续灌，
    #      否则部分数据 entities>0 会让 --if-empty 永久跳过）
    marker_existed = os.path.exists(REINDEX_MARKER)
    if args.if_empty:
        if not marker_existed and not milvus_is_empty():
            print('Milvus 已有向量数据且无中断标记，跳过重索引（--if-empty）。')
            sys.exit(0)
        if marker_existed:
            print(f'检测到未完成的重灌中断标记（{REINDEX_MARKER}），将强制全量重灌。')

    # 重建失败（Milvus 连不上等）以非零码退出，供部署流水线感知
    if not rebuild_collection():
        sys.exit(1)

    # 标记生命周期：循环前创建，仅在正常完成时删除；
    # 异常/被杀（KeyboardInterrupt / SystemExit / SIGKILL）则留存，
    # 下次 --if-empty 强制全量重灌。
    with open(REINDEX_MARKER, 'w') as f:
        f.write('reindex started at %s\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
    try:
        reindex_all()
    except BaseException:
        logger.error(
            '重灌被中断，标记留存，下次 --if-empty 将强制全量重灌：%s',
            REINDEX_MARKER,
        )
        raise
    os.remove(REINDEX_MARKER)
    print('重灌正常完成，中断标记已清除。')
