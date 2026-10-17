"""自前の論文検索モデル(TF-IDF)。

PubMed に毎回検索を任せるのではなく、あらかじめ保存した抄録(corpus)に対して、
こちらで索引を作り、質問との近さを計算して順位を付ける。

  1. 日本語の質問を、英語の検索語に変換する(日英の橋渡し)
  2. 論文(タイトル + 抄録)を TF-IDF のベクトルにする
  3. 質問ベクトルとのコサイン類似度で並べる

比較用に、従来のキーワード一致(質問の語が何個含まれるか)も用意してある。
どちらが良いかは evaluate.py で、チームが付けた正解データを使って数字で比べる。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from . import research

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CORPUS = DATA_DIR / "corpus.jsonl"

SAFETY_NOTE = (
    "これは文献検索であり、診断・治療・個別の安全判断ではありません。"
    "持病、妊娠中、服薬中、症状がある場合は医療者に相談してください。"
)

# research.py の辞書(サウナ・水風呂・睡眠など)に、検索の取りこぼしが出やすい話題を足す。
# (日本語のキーワード, 英語の検索語)
EXTRA_EXPANSIONS = (
    (("血圧", "高血圧"), "blood pressure hypertension"),
    (("脱水", "発汗", "汗", "水分"), "dehydration sweat fluid loss"),
    (("電解質", "ナトリウム", "カリウム", "塩分", "ミネラル"), "electrolyte sodium potassium"),
    (("飲酒", "アルコール", "お酒"), "alcohol"),
    (("妊娠", "妊婦"), "pregnancy"),
    (("自律神経", "心拍変動", "副交感", "交感神経"), "autonomic nervous heart rate variability"),
    (("死亡", "寿命", "長生き"), "mortality"),
    (("うつ", "ストレス", "気分", "メンタル"), "depression stress mood"),
    (("ととのう", "リラックス", "リラクゼーション"), "relaxation"),
    (("不整脈", "心不全", "狭心症", "心筋梗塞"), "arrhythmia heart failure angina myocardial infarction"),
)

_TOKEN = re.compile(r"[a-z0-9]+")

# research.py の辞書は PubMed 用の検索式("(sauna OR ...)")なので、OR・括弧を取り除いて語だけにする。
TOPIC_EXPANSIONS_EN = tuple(
    (words, re.sub(r"\b(OR|AND)\b|[()]", " ", query)) for words, query in research.TOPIC_EXPANSIONS
)


def stem(word: str) -> str:
    """とても簡単な語尾の正規化。文書と質問に同じ処理を使えば十分機能する。"""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def analyze(text: str) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    return [stem(t) for t in tokens if t not in ENGLISH_STOP_WORDS and len(t) > 1]


def query_to_english(question: str) -> list[str]:
    """日本語(または英語)の質問から、検索に使う英語の語を取り出す。

    辞書にある話題も、英語の単語も含まれない質問(「今日の天気は」など)は空を返す。
    research.py の検索は、このとき「サウナ」を既定にしてしまうが、ここでは無関係な論文を
    返さないよう、あえて何も返さない。
    """
    lowered = question.lower()
    parts = [english for words, english in TOPIC_EXPANSIONS_EN if any(w in lowered for w in words)]
    parts += [english for words, english in EXTRA_EXPANSIONS if any(w in question for w in words)]
    parts += re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", question)
    terms = analyze(" ".join(parts))
    return list(dict.fromkeys(terms))  # 重複を除き、順序は保つ


@dataclass(frozen=True)
class Doc:
    pmid: str
    title: str
    journal: str
    published: str
    authors: str
    publication_types: tuple[str, ...]
    abstract: str


class CorpusMissing(RuntimeError):
    """論文データ(corpus)がまだ作られていない。"""


def corpus_path() -> Path:
    return Path(os.environ.get("SAUNA_CORPUS", str(DEFAULT_CORPUS)))


def load_corpus(path: Optional[Path] = None) -> list[Doc]:
    path = Path(path) if path else corpus_path()
    if not path.exists():
        raise CorpusMissing(
            "論文データがまだありません。ターミナルで  npm run corpus  を実行して、PubMed から保存してください。"
        )
    docs = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            docs.append(Doc(
                pmid=str(row["pmid"]),
                title=row.get("title", ""),
                journal=row.get("journal", ""),
                published=row.get("published", ""),
                authors=row.get("authors", ""),
                publication_types=tuple(row.get("publication_types", [])),
                abstract=row.get("abstract", ""),
            ))
    return docs


class Index:
    """論文の TF-IDF 索引。"""

    def __init__(self, docs: list[Doc]):
        if not docs:
            raise CorpusMissing("論文データが空です。npm run corpus を実行してください。")
        self.docs = docs
        # タイトルは抄録より重要なので 2 回入れて重みを上げる。
        texts = [f"{d.title} {d.title} {d.abstract}" for d in docs]
        self.vectorizer = TfidfVectorizer(analyzer=analyze, sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(texts)
        self.doc_terms = [set(analyze(f"{d.title} {d.abstract}")) for d in docs]

    def rank_tfidf(self, terms: list[str], k: int = 5) -> list[tuple[Doc, float]]:
        """質問との類似度が高い順。類似度 0(一致する語なし)の論文は返さない。"""
        if not terms:
            return []
        q = self.vectorizer.transform([" ".join(terms)])
        scores = (self.matrix @ q.T).toarray().ravel()
        order = np.argsort(-scores, kind="stable")
        return [(self.docs[i], float(scores[i])) for i in order[:k] if scores[i] > 0]

    def rank_keyword_overlap(self, terms: list[str], k: int = 5) -> list[tuple[Doc, float]]:
        """比較用の基準: 質問の語のうち、何個がタイトル・抄録に含まれるか。"""
        if not terms:
            return []
        wanted = set(terms)
        scores = np.array([len(wanted & dt) for dt in self.doc_terms], dtype=float)
        order = np.argsort(-scores, kind="stable")
        return [(self.docs[i], float(scores[i])) for i in order[:k] if scores[i] > 0]


_cache: dict[tuple[str, float], Index] = {}


def get_index(path: Optional[Path] = None) -> Index:
    """索引を作って使い回す。corpus ファイルが更新されたら作り直す。"""
    path = Path(path) if path else corpus_path()
    if not path.exists():
        raise CorpusMissing(
            "論文データがまだありません。ターミナルで  npm run corpus  を実行して、PubMed から保存してください。"
        )
    key = (str(path), path.stat().st_mtime)
    if key not in _cache:
        _cache.clear()
        _cache[key] = Index(load_corpus(path))
    return _cache[key]


def search(question: str, k: int = 5, path: Optional[Path] = None) -> dict:
    index = get_index(path)
    terms = query_to_english(question)
    hits = index.rank_tfidf(terms, k)
    term_set = set(terms)

    articles = [
        {
            "pmid": d.pmid,
            "title": d.title,
            "journal": d.journal,
            "published": d.published,
            "authors": d.authors,
            "publication_types": list(d.publication_types),
            "abstract_excerpt": research.relevant_excerpt(d.abstract, term_set),
            "url": f"{research.PUBMED_URL}{d.pmid}/",
            "score": round(score, 3),
        }
        for d, score in hits
    ]
    return {
        "question": question,
        "method": "tfidf-local",
        "query_terms": terms,
        "corpus_size": len(index.docs),
        "answer": (
            f"保存済みの論文 {len(index.docs)} 件から、質問に近い論文を {len(articles)} 件選びました。"
            "各論文の書誌情報と抄録の抜粋を確認し、結論は原著・レビュー本文で検証してください。"
            if articles else
            "近い論文が見つかりませんでした。サウナ・水風呂・睡眠・血圧などの語を含めて質問し直してください。"
        ),
        "safety_note": SAFETY_NOTE,
        "articles": articles,
    }
