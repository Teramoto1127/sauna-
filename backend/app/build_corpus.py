"""PubMed から論文の抄録を集めて、手元に保存する。

実行:  npm run corpus
保存先: backend/data/corpus.jsonl(1行1論文)と backend/data/corpus.meta.json

抄録の著作権は出版社にあるため、保存したファイルは Git に入れない(.gitignore 済み)。
画面に出すのも関連する数文の抜粋だけにしている。各自のパソコンで作り直してもらう。
集める論文を増やしたいときは、下の SEED_QUERIES に PubMed の検索式を足す。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from xml.etree import ElementTree

from . import research
from .retrieval import DATA_DIR, corpus_path

# 1つの検索式あたりに集める最大件数(関連度順)。
PER_QUERY = 60
# PubMed への負荷を抑える。NCBI は API キーなしで 1 秒あたり 3 回までを求めている。
PAUSE_SECONDS = 0.5
FETCH_BATCH = 50

SEED_QUERIES = (
    ("心血管・死亡率", '(sauna OR "sauna bathing") AND (cardiovascular OR mortality OR "coronary heart disease")'),
    ("血圧・高血圧", '(sauna OR "sauna bathing") AND ("blood pressure" OR hypertension)'),
    ("睡眠", '(sauna OR "sauna bathing" OR "hot water bath") AND (sleep OR insomnia)'),
    ("自律神経・心拍変動", '(sauna OR "heat exposure") AND ("heart rate variability" OR "autonomic nervous system")'),
    ("サウナの安全性・禁忌", '(sauna OR "sauna bathing") AND (safety OR "adverse effects" OR contraindications OR alcohol)'),
    ("サウナのレビュー", '(sauna OR "sauna bathing") AND (review[pt] OR "systematic review" OR meta-analysis[pt])'),
    ("水風呂・寒冷ショック", '("cold water immersion" OR "cold shock response") AND (safety OR cardiovascular OR risk)'),
    ("冷水浴と回復", '("cold water immersion" OR "cold exposure") AND (recovery OR stress OR mood)'),
    ("温冷交代浴", '(sauna OR "heat exposure") AND ("cold water immersion" OR "contrast")'),
    ("発汗・脱水・電解質", '(sauna OR "heat exposure") AND (sweat OR dehydration OR electrolyte OR "fluid loss")'),
    ("メンタル・ストレス", '(sauna OR "sauna bathing") AND (depression OR stress OR mood OR anxiety)'),
    ("妊娠・特別な集団", '(sauna OR "sauna bathing") AND (pregnancy OR elderly OR "heart failure")'),
)


def _ids(term: str) -> list[str]:
    xml = research._request("esearch.fcgi", {
        "db": "pubmed", "retmode": "xml", "sort": "relevance", "retmax": str(PER_QUERY), "term": term,
    })
    try:
        return [n.text for n in ElementTree.fromstring(xml).findall(".//Id") if n.text]
    except ElementTree.ParseError as exc:
        raise research.ResearchUnavailable("PubMed の検索結果を読み取れませんでした。") from exc


def main() -> int:
    seen: dict[str, research.Article] = {}
    per_topic: dict[str, int] = {}

    for label, term in SEED_QUERIES:
        try:
            ids = [i for i in _ids(term) if i not in seen]
        except research.ResearchUnavailable as exc:
            print(f"× {label}: {exc}", file=sys.stderr)
            return 1
        added = 0
        for start in range(0, len(ids), FETCH_BATCH):
            time.sleep(PAUSE_SECONDS)
            batch = ids[start:start + FETCH_BATCH]
            try:
                xml = research._request("efetch.fcgi", {
                    "db": "pubmed", "retmode": "xml", "id": ",".join(batch),
                })
                articles = research._parse_articles(xml)
            except research.ResearchUnavailable as exc:
                print(f"× {label}: {exc}", file=sys.stderr)
                return 1
            for a in articles:
                if a.abstract and a.pmid not in seen:  # 抄録のない論文は検索に使えない
                    seen[a.pmid] = a
                    added += 1
        per_topic[label] = added
        print(f"✓ {label}: 新しく {added} 件(累計 {len(seen)} 件)")
        time.sleep(PAUSE_SECONDS)

    if not seen:
        print("論文を1件も取得できませんでした。", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = corpus_path()
    with path.open("w", encoding="utf-8") as f:
        for a in seen.values():
            f.write(json.dumps({
                "pmid": a.pmid, "title": a.title, "journal": a.journal, "published": a.published,
                "authors": a.authors, "publication_types": list(a.publication_types), "abstract": a.abstract,
            }, ensure_ascii=False) + "\n")
    path.with_name("corpus.meta.json").write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "n_articles": len(seen),
        "per_topic_added": per_topic,
        "queries": dict(SEED_QUERIES),
        "source": "PubMed (NCBI E-utilities)",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(seen)} 件を {path} に保存しました。")
    print("次は backend/data/eval_queries.json の relevant_pmids に正解を付けて、npm run eval を実行します。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
