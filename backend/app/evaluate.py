"""論文検索の評価。チームが付けた正解データで、検索方式どうしを数字で比べる。

実行:  npm run eval

比べるもの
  keyword : 質問の語が論文に何個含まれるか(基準)
  tfidf   : 自前の TF-IDF(retrieval.py)

指標(どれも 0〜1 で、大きいほど良い)
  Hit@1    1位が正解だった割合
  Recall@5 正解のうち、上位5件に入った割合
  MRR      最初の正解が何位にあったか(1位なら1、2位なら0.5、10位以内になければ0)

評価に使う質問は backend/data/eval_queries.json。relevant_pmids が空の質問は、
正解がまだ付いていないので数に入れない。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import retrieval

DEFAULT_QUERIES = retrieval.DATA_DIR / "eval_queries.json"
CUTOFF = 10


def hit_at_1(ranked: list[str], relevant: set[str]) -> float:
    return 1.0 if ranked[:1] and ranked[0] in relevant else 0.0


def recall_at_k(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str], cutoff: int = CUTOFF) -> float:
    for rank, pmid in enumerate(ranked[:cutoff], start=1):
        if pmid in relevant:
            return 1.0 / rank
    return 0.0


def load_queries(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["queries"] if isinstance(data, dict) else data


def evaluate(index: retrieval.Index, queries: list[dict]) -> dict:
    methods = {
        "keyword": index.rank_keyword_overlap,
        "tfidf": index.rank_tfidf,
    }
    labeled = [q for q in queries if q.get("relevant_pmids")]
    totals = {m: {"hit@1": 0.0, "recall@5": 0.0, "mrr": 0.0} for m in methods}
    per_query = []

    for q in labeled:
        relevant = {str(p) for p in q["relevant_pmids"]}
        terms = retrieval.query_to_english(q["question"])
        row = {"id": q.get("id", ""), "question": q["question"], "n_relevant": len(relevant)}
        for name, rank in methods.items():
            ranked = [d.pmid for d, _ in rank(terms, CUTOFF)]
            scores = {
                "hit@1": hit_at_1(ranked, relevant),
                "recall@5": recall_at_k(ranked, relevant),
                "mrr": reciprocal_rank(ranked, relevant),
            }
            for key, value in scores.items():
                totals[name][key] += value
            row[name] = scores
        per_query.append(row)

    n = len(labeled)
    averages = {m: {k: (v / n if n else None) for k, v in t.items()} for m, t in totals.items()}
    return {
        "n_queries": len(queries),
        "n_labeled": n,
        "n_unlabeled": len(queries) - n,
        "corpus_size": len(index.docs),
        "average": averages,
        "per_query": per_query,
    }


def print_report(result: dict, verbose: bool = False) -> None:
    print(f"論文 {result['corpus_size']} 件 / 質問 {result['n_queries']} 件"
          f"(正解あり {result['n_labeled']} 件、正解なし {result['n_unlabeled']} 件)")
    if result["n_labeled"] == 0:
        print("\n正解が付いた質問がありません。eval_queries.json の relevant_pmids に PMID を入れてください。")
        return
    if result["n_labeled"] < 20:
        print(f"※ 正解ありの質問が {result['n_labeled']} 件と少ないため、差は偶然の可能性があります(目安: 20件以上)。")
    print(f"\n{'方式':<10}{'Hit@1':>9}{'Recall@5':>11}{'MRR':>9}")
    for name, a in result["average"].items():
        print(f"{name:<10}{a['hit@1']:>9.3f}{a['recall@5']:>11.3f}{a['mrr']:>9.3f}")
    if verbose:
        print("\n質問ごと(MRR: keyword → tfidf)")
        for r in result["per_query"]:
            print(f"  {r['id']:<6}{r['keyword']['mrr']:.2f} → {r['tfidf']['mrr']:.2f}  {r['question']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="論文検索の評価")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="質問ごとの結果も表示")
    args = parser.parse_args(argv)

    try:
        index = retrieval.get_index(args.corpus)
    except retrieval.CorpusMissing as exc:
        print(exc, file=sys.stderr)
        return 1
    if not args.queries.exists():
        print(f"質問ファイルがありません: {args.queries}", file=sys.stderr)
        return 1
    print_report(evaluate(index, load_queries(args.queries)), args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
