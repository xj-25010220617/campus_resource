"""检索效果评测（离线回归）。

用途：给 RAG 调参提供量化依据。批量跑 rag.retrieve()，统计
Recall@1/3/5、MRR、零召回率、平均耗时，可与历史结果对比做回归检测。

样本来源（二选一）：
1. --dataset 指定人工标注文件（推荐，质量最高）：
   [
     {"question": "15级优秀毕业生有哪些人", "expected_doc_ids": [314]},
     {"question": "学费多少钱", "expected_titles": ["2025年收费标准"]}
   ]
2. 不传 --dataset 时自动从知识库构建：用文档标题当问题，期望命中自己。
   这衡量的是「标题可检索性」，是基线而非真实问法。

用法示例：
    python scripts/eval_retrieval.py --limit 50
    python scripts/eval_retrieval.py --dataset scripts/eval_dataset.json
    python scripts/eval_retrieval.py --retry 0 --baseline scripts/eval_results/xxx.json
    python scripts/eval_retrieval.py --no-rewrite --role student

退出码：0 正常；1 相对 baseline 的 Recall@5 跌幅超过阈值（可用于 CI 卡点）。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

RESULT_DIR = Path(__file__).resolve().parent / 'eval_results'

# 文档标题里常见的后缀，构建 auto 样本时去掉
_EXT_RE = re.compile(r'\.(pdf|docx?|pptx?|xlsx?|txt|md|png|jpe?g)$', re.IGNORECASE)
_PUNCT_RE = re.compile(r'[\s\-_—()（）\[\]【】《》]+')


def _setup_django():
    """延迟初始化 Django 并返回 rag 模块，便于脚本自身单测。"""
    import django
    django.setup()
    from utils import rag
    return rag



def build_auto_dataset(limit: int, min_title_len: int) -> list:
    """用知识库文档标题构建基线样本。"""
    from apps.knowledge.models import KnowledgeDoc

    docs = (
        KnowledgeDoc.objects
        .filter(status='indexed', chunk_count__gt=0, is_sensitive=False)
        .exclude(title='')
        .order_by('-created_at')
    )
    items = []
    for doc in docs.iterator():
        title = _EXT_RE.sub('', doc.title).strip()
        if len(title) < min_title_len:
            continue
        items.append({
            'question': title,
            'expected_doc_ids': [doc.id],
            'source': 'auto',
        })
        if len(items) >= limit:
            break
    return items


def load_dataset(path: str, limit: int) -> list:
    """读取人工标注样本。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError('数据集必须是 JSON 数组')
    items = []
    for row in data[:limit]:
        if not isinstance(row, dict) or not row.get('question'):
            continue
        row.setdefault('expected_doc_ids', [])
        row.setdefault('source', 'manual')
        items.append(row)
    return items


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub('', (text or '')).lower()


def _match_rank(hits: list, expected_ids: list, expected_titles: list) -> int:
    """返回首个命中的 1-based 排名，未命中返回 0。"""
    ids = set(expected_ids or [])
    titles = [_normalize(t) for t in (expected_titles or []) if t]
    for idx, hit in enumerate(hits, start=1):
        if hit.get('doc_id') in ids:
            return idx
        if titles:
            hit_title = _normalize(hit.get('doc_title', ''))
            if any(t and t in hit_title for t in titles):
                return idx
    return 0


def run_eval(items: list, user_context: dict, top_k: int, use_parse: bool, rag_module) -> dict:
    """逐条检索并汇总指标。"""
    rag = rag_module
    parse = None
    if use_parse:
        from apps.classify.utils.query_parser import parse as parse_fn
        parse = parse_fn

    details = []
    ranks = []
    zero_recall = 0
    errors = 0
    elapsed_list = []
    hit_counts = []

    total = len(items)
    for i, item in enumerate(items, start=1):
        question = item['question']
        filters = item.get('parsed_filters') or {}
        if parse and not filters:
            try:
                filters = parse(question, user_context) or {}
            except Exception as e:  # 解析失败不应中断评测
                print(f'  [warn] 解析失败，按空过滤处理: {e}')
                filters = {}

        start = time.perf_counter()
        try:
            hits = rag.retrieve(
                question=question,
                user_context=user_context,
                parsed_filters=filters,
                top_k=top_k,
            )
        except Exception as e:
            errors += 1
            hits = []
            print(f'  [error] ({i}/{total}) {question[:30]}: {e}')
        cost_ms = (time.perf_counter() - start) * 1000
        elapsed_list.append(cost_ms)
        hit_counts.append(len(hits))

        rank = _match_rank(
            hits, item.get('expected_doc_ids'), item.get('expected_titles'),
        )
        if rank == 0:
            if not hits:
                zero_recall += 1
        else:
            ranks.append(rank)

        details.append({
            'question': question,
            'expected_doc_ids': item.get('expected_doc_ids', []),
            'rank': rank,
            'hit_count': len(hits),
            'top_doc_ids': [h.get('doc_id') for h in hits[:5]],
            'top_score': round(max((h.get('score', 0) for h in hits), default=0.0), 4),
            'elapsed_ms': round(cost_ms, 1),
            'source': item.get('source', 'manual'),
        })
        if i % 10 == 0 or i == total:
            print(f'  进度 {i}/{total}')

    def recall_at(k):
        return sum(1 for d in details if 0 < d['rank'] <= k) / total if total else 0.0

    mrr = sum(1.0 / d['rank'] for d in details if d['rank'] > 0) / total if total else 0.0

    return {
        'samples': total,
        'errors': errors,
        'recall@1': round(recall_at(1), 4),
        'recall@3': round(recall_at(3), 4),
        'recall@5': round(recall_at(5), 4),
        'mrr@5': round(mrr, 4),
        'zero_recall_rate': round(zero_recall / total, 4) if total else 0.0,
        'avg_elapsed_ms': round(sum(elapsed_list) / total, 1) if total else 0.0,
        'max_elapsed_ms': round(max(elapsed_list), 1) if elapsed_list else 0.0,
        'avg_hit_count': round(sum(hit_counts) / total, 2) if total else 0.0,
        'details': details,
    }


def print_report(metrics: dict, baseline: dict = None) -> None:
    rows = [
        ('样本数', 'samples', None, None),
        ('异常数', 'errors', None, None),
        ('Recall@1', 'recall@1', 'pct', 0.01),
        ('Recall@3', 'recall@3', 'pct', 0.01),
        ('Recall@5', 'recall@5', 'pct', 0.01),
        ('MRR@5', 'mrr@5', 'num', 0.01),
        ('零召回率', 'zero_recall_rate', 'pct', -0.01),
        ('平均耗时(ms)', 'avg_elapsed_ms', 'ms', -1.0),
        ('平均命中数', 'avg_hit_count', 'num', None),
    ]
    print('\n' + '=' * 68)
    print(f"{'指标':<14}{'本次':>12}{'基线':>12}{'变化':>14}")
    print('-' * 68)
    for label, key, kind, warn_delta in rows:
        cur = metrics.get(key)
        base = baseline.get(key) if baseline else None
        if kind == 'pct':
            cur_s = f'{cur * 100:.1f}%'
            base_s = f'{base * 100:.1f}%' if base is not None else '-'
            delta = f'{(cur - base) * 100:+.1f}pt' if base is not None else '-'
        else:
            cur_s = f'{cur}'
            base_s = f'{base}' if base is not None else '-'
            delta = f'{cur - base:+.2f}' if base is not None else '-'
        flag = ''
        if base is not None and warn_delta is not None:
            diff = cur - base
            if warn_delta > 0 and diff < -abs(warn_delta):
                flag = '  ⚠ 下降'
            elif warn_delta < 0 and diff > abs(warn_delta):
                flag = '  ⚠ 上升'
        print(f'{label:<14}{cur_s:>12}{base_s:>12}{delta:>14}{flag}')
    print('=' * 68)


def main() -> int:
    parser = argparse.ArgumentParser(description='RAG 检索效果评测')
    parser.add_argument('--dataset', help='人工标注样本 JSON 路径')
    parser.add_argument('--limit', type=int, default=50, help='样本数量上限（默认 50）')
    parser.add_argument('--role', default='super_admin', help='检索使用的用户角色')
    parser.add_argument('--user-id', type=int, default=None,
                        help='检索使用的用户 ID（非管理员角色建议指定，否则只能检索校级文档）')
    parser.add_argument('--college-id', type=int, default=None,
                        help='检索使用的学院 ID（学院管理员/学生角色建议指定）')
    parser.add_argument('--top-k', type=int, default=None,
                        help='召回数量（默认取 rag.RAG_TOP_K）')
    parser.add_argument('--retry', type=int, choices=[0, 1], default=1,
                        help='是否启用 Agentic 二次检索（默认 1）')
    parser.add_argument('--no-rewrite', action='store_true',
                        help='禁用 LLM Query 改写，保证评测可重复、不消耗 token')
    parser.add_argument('--no-parse', action='store_true',
                        help='不做 LLM 过滤条件解析，直接用空过滤')
    parser.add_argument('--no-rerank', action='store_true',
                        help='禁用 Cross-Encoder 精排。服务器内存紧张时可避免加载模型')
    parser.add_argument('--with-rerank', action='store_true',
                        help='显式启用 Cross-Encoder 精排（默认沿用 settings 配置）')
    parser.add_argument('--min-title-len', type=int, default=6,
                        help='自动构建样本时标题的最小长度')
    parser.add_argument('--baseline', help='历史结果 JSON，用于对比回归')
    parser.add_argument('--regression-threshold', type=float, default=0.02,
                        help='Recall@5 跌幅超过该值则退出码为 1（默认 0.02）')
    parser.add_argument('--no-save', action='store_true', help='不保存结果文件')
    parser.add_argument('--milvus-uri', default=None,
                        help='覆盖 Milvus 库路径。线上服务会锁定 ~/.campus_milvus.db，'
                             '评测时应先复制一份副本再指向副本，避免影响线上')
    args = parser.parse_args()

    rag = _setup_django()

    from django.conf import settings

    if args.milvus_uri:
        settings.MILVUS_URI = args.milvus_uri
        print(f'Milvus 使用副本库: {args.milvus_uri}')

    if args.no_rerank:
        settings.CROSS_ENCODER_ENABLED = False
        print('Cross-Encoder 精排: 已禁用')
    elif args.with_rerank:
        settings.CROSS_ENCODER_ENABLED = True
        print('Cross-Encoder 精排: 已启用')

    top_k = args.top_k or rag.RAG_TOP_K

    # 参数化检索行为
    rag.RAG_RETRY_MAX_ATTEMPTS = args.retry
    if args.no_rewrite:
        import utils.query_rewriter as qrw
        qrw.rewrite_query = lambda q, use_cache=True: [q]

    print(f'构建样本（dataset={args.dataset or "auto"}）...')
    items = load_dataset(args.dataset, args.limit) if args.dataset \
        else build_auto_dataset(args.limit, args.min_title_len)
    if not items:
        print('未构建出任何样本，请检查知识库是否已索引文档，或提供 --dataset')
        return 0

    print(f'开始评测 {len(items)} 条 | role={args.role} top_k={top_k} '
          f'retry={args.retry} rewrite={not args.no_rewrite}')
    user_context = {'role': args.role}
    if args.user_id:
        user_context['user_id'] = args.user_id
    if args.college_id:
        user_context['college_id'] = args.college_id
    metrics = run_eval(items, user_context, top_k, use_parse=not args.no_parse,
                       rag_module=rag)

    baseline = None
    if args.baseline:
        with open(args.baseline, 'r', encoding='utf-8') as f:
            baseline = json.load(f).get('metrics')

    print_report(metrics, baseline)

    if not args.no_save:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULT_DIR / f'eval_{datetime.now():%Y%m%d_%H%M%S}.json'
        payload = {
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'config': {
                'role': args.role,
                'user_id': args.user_id,
                'college_id': args.college_id,
                'top_k': top_k,
                'retry': args.retry,
                'rewrite': not args.no_rewrite,
                'rerank': not args.no_rerank,
                'dataset': args.dataset or 'auto',
            },
            'metrics': {k: v for k, v in metrics.items() if k != 'details'},
            'details': metrics['details'],
        }
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f'结果已保存: {out}')

    if baseline:
        drop = baseline.get('recall@5', 0) - metrics['recall@5']
        if drop > args.regression_threshold:
            print(f'⚠ Recall@5 相对基线下跌 {drop * 100:.1f}pt，超过阈值 '
                  f'{args.regression_threshold * 100:.1f}pt')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
