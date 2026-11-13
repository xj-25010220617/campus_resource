"""检索效果评测（离线回归）。

用途：给 RAG 调参提供量化依据。批量跑 rag.retrieve()，统计
Recall@1/3/5/10、MRR、nDCG@5、上下文准确率、零召回率、平均耗时，
可与历史结果对比做回归检测。

样本来源（三选一）：
1. --dataset 指定人工标注文件（推荐，质量最高），支持 JSON 数组与 JSONL：
   [
     {"question": "15级优秀毕业生有哪些人", "expected_doc_ids": [314], "type": "list"},
     {"question": "学费多少钱", "expected_titles": ["2025年收费标准"], "type": "policy"}
   ]
   JSONL 每行一条，兼容简写键：q / doc_ids。
2. --from-query-log N：从 QueryLog 抽取真实高频问题，生成待标注的 JSONL 模板
   （expected_doc_ids 留空，人工补齐后再用 --dataset 跑）。
3. 不传 --dataset 时自动从知识库构建：用文档标题当问题，期望命中自己。
   这衡量的是「标题可检索性」，是基线而非真实问法。

用法示例：
    python scripts/eval_retrieval.py --limit 50
    python scripts/eval_retrieval.py --dataset scripts/golden_set.jsonl
    python scripts/eval_retrieval.py --dataset scripts/eval_dataset.json
    python scripts/eval_retrieval.py --retry 0 --baseline scripts/eval_results/xxx.json
    python scripts/eval_retrieval.py --no-rewrite --role student
    python scripts/eval_retrieval.py --from-query-log 200

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
from typing import Optional

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
        # created_at 常有并列（批量导入），必须再用 id 兜底，否则每次
        # 取到的样本集不同，前后两次评测不可比。
        .order_by('-created_at', '-id')
    )
    items = []
    for doc in docs.iterator():
        title = _EXT_RE.sub('', doc.title).strip()
        if len(title) < min_title_len:
            continue
        items.append({
            'question': title,
            'expected_doc_ids': [doc.id],
            'type': 'auto_title',
            'source': 'auto',
        })
        if len(items) >= limit:
            break
    return items


def _normalize_row(row: dict) -> Optional[dict]:
    """统一样本字段（兼容 question/q、expected_doc_ids/doc_ids 两种写法）。"""
    if not isinstance(row, dict):
        return None
    question = (row.get('question') or row.get('q') or '').strip()
    if not question:
        return None
    expected = row.get('expected_doc_ids') or row.get('doc_ids') or []
    return {
        'question': question,
        'expected_doc_ids': [int(i) for i in expected if str(i).isdigit()],
        'expected_titles': row.get('expected_titles') or [],
        'type': row.get('type') or 'unlabeled',
        'answer_points': row.get('answer_points') or [],
        'must_cite': bool(row.get('must_cite', False)),
        'parsed_filters': row.get('parsed_filters') or {},
        'source': row.get('source', 'manual'),
    }


def load_dataset(path: str, limit: int) -> list:
    """读取人工标注样本（.jsonl / .json 均可）。"""
    text = Path(path).read_text(encoding='utf-8')
    if path.lower().endswith('.jsonl'):
        raw = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                print(f'  [warn] 跳过无法解析的行: {line[:60]}')
    else:
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError('数据集必须是 JSON 数组或 JSONL')
        raw = data

    items = []
    for row in raw[:limit]:
        item = _normalize_row(row)
        if item:
            items.append(item)
    return items


def build_from_query_log(limit: int, min_hits: int) -> list:
    """从 QueryLog 抽取真实问题，生成待人工标注的样本模板。

    只做去重与频次排序，expected_doc_ids 留空——金标必须人工确认，
    不能拿系统自己的召回结果当答案（否则评测变成自证）。
    """
    from django.db.models import Count, Min
    from apps.chat.models import QueryLog

    rows = (
        QueryLog.objects
        .filter(hit_count__gte=min_hits)
        .values('question')
        .annotate(freq=Count('id'), first_at=Min('created_at'))
        .order_by('-freq', '-first_at')[:limit]
    )
    items = []
    for row in rows:
        question = (row['question'] or '').strip()
        if len(question) < 4:
            continue
        items.append({
            'q': question,
            'type': 'unlabeled',
            'doc_ids': [],
            'answer_points': [],
            'must_cite': True,
            'freq': row['freq'],
        })
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


def _hit_is_relevant(hit: dict, ids: set, titles: list) -> bool:
    """判断单条召回是否属于标注的相关文档（doc_id 精确匹配或标题包含匹配）。"""
    if hit.get('doc_id') in ids:
        return True
    if titles:
        hit_title = _normalize(hit.get('doc_title', ''))
        return any(t and t in hit_title for t in titles)
    return False


def _ndcg_at_k(hits: list, ids: set, titles: list, k: int) -> Optional[float]:
    """文档级二值相关性的 nDCG@k；样本无标注时返回 None（不参与平均）。

    同一文档的多个分块只计一次——金标标注的是「哪几篇文档相关」，
    重复计分会让 DCG 超过 IDCG（nDCG > 1）。
    """
    if not ids and not titles:
        return None
    import math

    dcg = 0.0
    seen_docs = set()
    rank = 0
    for hit in hits:
        doc_key = hit.get('doc_id') or hit.get('doc_title')
        if doc_key in seen_docs:
            continue
        seen_docs.add(doc_key)
        rank += 1
        if rank > k:
            break
        if _hit_is_relevant(hit, ids, titles):
            dcg += 1.0 / math.log2(rank + 1)
    # IDCG：理想情况下相关文档排在最前，数量取标注文档数与 k 的较小值
    ideal_n = min(max(len(ids), len(titles)), k) or 1
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_n + 1))
    return dcg / idcg if idcg > 0 else 0.0


def run_eval(items: list, user_context: dict, top_k: int, use_parse: bool, rag_module) -> dict:
    """逐条检索并汇总指标。"""
    rag = rag_module
    parse = None
    if use_parse:
        from apps.classify.utils.query_parser import parse as parse_fn
        parse = parse_fn

    details = []
    zero_recall = 0
    errors = 0
    elapsed_list = []
    hit_counts = []
    ndcgs = []
    precisions = []
    by_type = {}

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

        ids = set(item.get('expected_doc_ids') or [])
        titles = [_normalize(t) for t in (item.get('expected_titles') or []) if t]
        rank = _match_rank(
            hits, item.get('expected_doc_ids'), item.get('expected_titles'),
        )
        if rank == 0 and not hits:
            zero_recall += 1

        labeled = bool(ids or titles)
        ndcg = _ndcg_at_k(hits, ids, titles, 5)
        if ndcg is not None:
            ndcgs.append(ndcg)
        # Context Precision：召回块里属于相关文档的比例（衡量上下文噪声）
        precision = None
        if labeled and hits:
            precision = sum(
                1 for h in hits if _hit_is_relevant(h, ids, titles)
            ) / len(hits)
            precisions.append(precision)

        group = item.get('type') or 'unlabeled'
        bucket = by_type.setdefault(group, {
            'samples': 0, 'hit@5': 0, 'labeled': 0, 'zero_recall': 0,
        })
        bucket['samples'] += 1
        if 0 < rank <= 5:
            bucket['hit@5'] += 1
        if labeled:
            bucket['labeled'] += 1
        if not hits:
            bucket['zero_recall'] += 1

        details.append({
            'question': question,
            'type': group,
            'expected_doc_ids': item.get('expected_doc_ids', []),
            'rank': rank,
            'ndcg@5': round(ndcg, 4) if ndcg is not None else None,
            'context_precision': round(precision, 4) if precision is not None else None,
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

    per_type = {}
    for group, bucket in by_type.items():
        n = bucket['samples']
        per_type[group] = {
            'samples': n,
            'recall@5': round(bucket['hit@5'] / n, 4) if n else 0.0,
            'zero_recall_rate': round(bucket['zero_recall'] / n, 4) if n else 0.0,
            'labeled': bucket['labeled'],
        }

    return {
        'samples': total,
        'errors': errors,
        'labeled_samples': len(ndcgs),
        'recall@1': round(recall_at(1), 4),
        'recall@3': round(recall_at(3), 4),
        'recall@5': round(recall_at(5), 4),
        'recall@10': round(recall_at(10), 4),
        'mrr@5': round(mrr, 4),
        'ndcg@5': round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None,
        'context_precision': (
            round(sum(precisions) / len(precisions), 4) if precisions else None
        ),
        'zero_recall_rate': round(zero_recall / total, 4) if total else 0.0,
        'avg_elapsed_ms': round(sum(elapsed_list) / total, 1) if total else 0.0,
        'max_elapsed_ms': round(max(elapsed_list), 1) if elapsed_list else 0.0,
        'avg_hit_count': round(sum(hit_counts) / total, 2) if total else 0.0,
        'per_type': per_type,
        'details': details,
    }


def print_report(metrics: dict, baseline: dict = None) -> None:
    rows = [
        ('样本数', 'samples', None, None),
        ('有标注样本', 'labeled_samples', None, None),
        ('异常数', 'errors', None, None),
        ('Recall@1', 'recall@1', 'pct', 0.01),
        ('Recall@3', 'recall@3', 'pct', 0.01),
        ('Recall@5', 'recall@5', 'pct', 0.01),
        ('Recall@10', 'recall@10', 'pct', 0.01),
        ('MRR@5', 'mrr@5', 'num', 0.01),
        ('nDCG@5', 'ndcg@5', 'num', 0.01),
        ('上下文准确率', 'context_precision', 'num', -0.02),
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
            cur_s = f'{cur * 100:.1f}%' if cur is not None else '-'
            base_s = f'{base * 100:.1f}%' if base is not None else '-'
            delta = f'{(cur - base) * 100:+.1f}pt' if base is not None and cur is not None else '-'
        elif kind == 'num' or kind == 'ms':
            cur_s = f'{cur}' if cur is not None else '-'
            base_s = f'{base}' if base is not None else '-'
            delta = f'{cur - base:+.2f}' if base is not None and cur is not None else '-'
        else:
            cur_s, base_s, delta = str(cur), str(base or '-'), '-'
        flag = ''
        if base is not None and warn_delta is not None and cur is not None:
            diff = cur - base
            if warn_delta > 0 and diff < -abs(warn_delta):
                flag = '  ⚠ 下降'
            elif warn_delta < 0 and diff > abs(warn_delta):
                flag = '  ⚠ 上升'
        print(f'{label:<14}{cur_s:>12}{base_s:>12}{delta:>14}{flag}')
    print('=' * 68)

    per_type = metrics.get('per_type') or {}
    if len(per_type) > 1 or 'unlabeled' not in per_type:
        print(f"\n{'类型':<14}{'样本':>8}{'Recall@5':>12}{'零召回率':>12}{'有标注':>8}")
        print('-' * 56)
        for group, row in sorted(per_type.items()):
            print(
                f"{group:<14}{row['samples']:>8}"
                f"{row['recall@5'] * 100:>11.1f}%"
                f"{row['zero_recall_rate'] * 100:>11.1f}%"
                f"{row['labeled']:>8}"
            )
        print('-' * 56)


def main() -> int:
    parser = argparse.ArgumentParser(description='RAG 检索效果评测')
    parser.add_argument('--dataset', help='人工标注样本 JSON / JSONL 路径')
    parser.add_argument('--limit', type=int, default=50, help='样本数量上限（默认 50）')
    parser.add_argument('--from-query-log', type=int, default=0, metavar='N',
                        help='从 QueryLog 抽取 N 条真实问题，生成待标注金标集模板 JSONL')
    parser.add_argument('--min-hits', type=int, default=1,
                        help='--from-query-log 时只取召回命中数不低于该值的问题')
    parser.add_argument('--golden-out', default=str(
        Path(__file__).resolve().parent / 'golden_set.jsonl'),
        help='--from-query-log 模板输出路径')
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
    parser.add_argument('--collection', default=None,
                        help='覆盖 Milvus 集合名（影子集合评测，如 campus_knowledge_v2）')
    parser.add_argument('--fusion', choices=['weighted', 'rrf'], default=None,
                        help='覆盖检索融合算法（默认取 settings.RETRIEVAL_FUSION）')
    args = parser.parse_args()

    rag = _setup_django()

    from django.conf import settings

    # 仅生成金标集模板，不跑检索
    if args.from_query_log:
        items = build_from_query_log(args.from_query_log, args.min_hits)
        if not items:
            print('QueryLog 中没有符合条件的记录（可调低 --min-hits 或先积累线上提问）')
            return 0
        out = Path(args.golden_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8') as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        print(f'已生成 {len(items)} 条待标注样本: {out}')
        print('下一步：人工补齐每行的 doc_ids 与 type，再用 --dataset 跑评测')
        return 0

    if args.milvus_uri:
        settings.MILVUS_URI = args.milvus_uri
        print(f'Milvus 使用副本库: {args.milvus_uri}')
    if args.collection:
        settings.MILVUS_COLLECTION = args.collection
        rag.milvus_client.COLLECTION_NAME = args.collection
        print(f'Milvus 集合: {args.collection}')
    if args.fusion:
        settings.RETRIEVAL_FUSION = args.fusion
        print(f'融合算法: {args.fusion}')

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
                'fusion': getattr(settings, 'RETRIEVAL_FUSION', 'weighted'),
                'rerank_backend': getattr(settings, 'RERANK_BACKEND', 'off'),
                'milvus_collection': getattr(
                    rag.milvus_client, 'COLLECTION_NAME', ''),
                'dataset': args.dataset or 'auto',
            },
            'metrics': {k: v for k, v in metrics.items() if k != 'details'},
            'details': metrics['details'],
        }
        # 固定名 eval_report.json 便于和方案/基线文档对照，另存带时间戳的归档
        for out in (
            RESULT_DIR / f'eval_{datetime.now():%Y%m%d_%H%M%S}.json',
            RESULT_DIR / 'eval_report.json',
        ):
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f'结果已保存: {RESULT_DIR / "eval_report.json"}')

    if baseline:
        drop = baseline.get('recall@5', 0) - metrics['recall@5']
        if drop > args.regression_threshold:
            print(f'⚠ Recall@5 相对基线下跌 {drop * 100:.1f}pt，超过阈值 '
                  f'{args.regression_threshold * 100:.1f}pt')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
