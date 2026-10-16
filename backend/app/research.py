"""PubMed を検索元にした、出典提示型の論文検索（軽量 RAG）。

LLM に医学的な結論を作らせるのではなく、質問を PubMed 用の検索語に広げ、
取得した抄録から質問に近い部分を抜き出して出典と一緒に返す。
そのため、回答の根拠を画面から必ずたどれる。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
import re
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/"
USER_AGENT = "totonoi-golden-ratio/0.1 (educational PubMed search)"
MAX_ARTICLES = 8


class ResearchUnavailable(RuntimeError):
    """PubMed に接続できない、または応答を解釈できない場合。"""


@dataclass(frozen=True)
class Article:
    pmid: str
    title: str
    journal: str
    published: str
    authors: str
    publication_types: tuple[str, ...]
    abstract: str

    def public(self, question_terms: set[str]) -> dict:
        """画面に返す情報。抄録全文ではなく関連箇所だけを返す。"""
        return {
            "pmid": self.pmid,
            "title": self.title,
            "journal": self.journal,
            "published": self.published,
            "authors": self.authors,
            "publication_types": list(self.publication_types),
            "abstract_excerpt": relevant_excerpt(self.abstract, question_terms),
            "url": f"{PUBMED_URL}{self.pmid}/",
        }


# 日本語で尋ねても PubMed の索引語に届くように、アプリの対象領域だけを補助する。
# 元の英文キーワードも検索語へ残すため、特定の語に閉じた辞書検索にはしない。
TOPIC_EXPANSIONS = (
    (("サウナ", "ロウリュ", "温浴", "熱"), "(sauna OR sauna bathing OR passive heating OR heat therapy)"),
    (("水風呂", "冷水", "冷却", "アイスバス"), "(cold water immersion OR cold exposure OR cold-water immersion)"),
    (("外気浴", "休憩", "回復"), "(recovery OR rest OR post-exercise recovery)"),
    (("心臓", "血圧", "循環", "心血管"), "(cardiovascular OR blood pressure OR heart)"),
    (("睡眠", "不眠"), "(sleep OR insomnia)"),
    (("安全", "危険", "禁忌", "持病"), "(safety OR adverse effects OR contraindications)"),
)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def build_search_term(question: str) -> tuple[str, set[str]]:
    """質問を PubMed 用の検索語へ変換し、再順位付け用の英単語も返す。"""
    lowered = question.lower()
    expansions = [query for words, query in TOPIC_EXPANSIONS if any(word in lowered for word in words)]
    english_words = re.findall(r"[a-zA-Z][a-zA-Z-]{2,}", question.lower())

    # 対象外の質問でも、日本語のみで空検索にならないようサウナ研究を起点にする。
    if not expansions:
        expansions.append("(sauna OR sauna bathing)")
    if english_words:
        expansions.append("(" + " OR ".join(dict.fromkeys(english_words[:8])) + ")")

    term = " AND ".join(expansions)
    ranking_terms = set(english_words)
    for expanded in expansions:
        ranking_terms.update(re.findall(r"[a-z]{3,}", expanded.lower()))
    return term, ranking_terms


def _request(path: str, params: dict[str, str]) -> bytes:
    url = f"{EUTILS}/{path}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"})
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310: fixed NCBI HTTPS endpoint
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ResearchUnavailable("PubMed に接続できませんでした。時間をおいて再試行してください。") from exc


def _element_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return _clean_text("".join(element.itertext()))


def _published(article: ElementTree.Element) -> str:
    date = article.find(".//ArticleDate") or article.find(".//JournalIssue/PubDate")
    if date is None:
        return ""
    year = _clean_text(date.findtext("Year")) or _clean_text(date.findtext("MedlineDate"))[:4]
    month = _clean_text(date.findtext("Month"))
    return " ".join(value for value in (year, month) if value)


def _authors(article: ElementTree.Element) -> str:
    people = []
    for author in article.findall(".//AuthorList/Author")[:3]:
        name = " ".join(filter(None, (_clean_text(author.findtext("ForeName")), _clean_text(author.findtext("LastName")))))
        if not name:
            name = _clean_text(author.findtext("CollectiveName"))
        if name:
            people.append(name)
    if not people:
        return ""
    return ", ".join(people) + (" et al." if len(article.findall(".//AuthorList/Author")) > 3 else "")


def _parse_articles(raw_xml: bytes) -> tuple[Article, ...]:
    try:
        root = ElementTree.fromstring(raw_xml)
    except ElementTree.ParseError as exc:
        raise ResearchUnavailable("PubMed の応答を読み取れませんでした。") from exc

    articles = []
    for record in root.findall(".//PubmedArticle"):
        medline = record.find("MedlineCitation")
        article = record.find(".//Article")
        if medline is None or article is None:
            continue
        pmid = _clean_text(medline.findtext("PMID"))
        title = _element_text(article.find("ArticleTitle"))
        if not pmid or not title:
            continue
        sections = []
        for section in article.findall(".//Abstract/AbstractText"):
            label = _clean_text(section.get("Label"))
            text = _element_text(section)
            if text:
                sections.append(f"{label}: {text}" if label else text)
        articles.append(Article(
            pmid=pmid,
            title=title,
            journal=_clean_text(article.findtext("Journal/Title")),
            published=_published(article),
            authors=_authors(article),
            publication_types=tuple(_clean_text(x.text) for x in article.findall(".//PublicationTypeList/PublicationType") if _clean_text(x.text)),
            abstract=" ".join(sections),
        ))
    return tuple(articles)


def _rank(articles: Iterable[Article], terms: set[str]) -> list[Article]:
    def score(article: Article) -> tuple[int, int]:
        haystack = f"{article.title} {article.abstract}".lower()
        matches = sum(term in haystack for term in terms)
        # 上位エビデンスを同点時に先に示す。検索順位は Python の安定ソートで保持する。
        is_review = any("review" in kind.lower() or "meta-analysis" in kind.lower() for kind in article.publication_types)
        return (matches, int(is_review))

    return sorted(articles, key=score, reverse=True)


def relevant_excerpt(abstract: str, terms: set[str]) -> str:
    """根拠確認用に、抄録の最も関連する最大2文だけを出す。"""
    if not abstract:
        return "PubMed に抄録が登録されていません。リンク先で書誌情報を確認してください。"
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    scored = [(sum(term in sentence.lower() for term in terms), index, sentence) for index, sentence in enumerate(sentences)]
    best = sorted(scored, key=lambda item: (-item[0], item[1]))[:2]
    excerpt = " ".join(sentence for _, _, sentence in sorted(best, key=lambda item: item[1])).strip()
    # UI 上で抄録を再配布しないための上限。意味の途中で切れたことも明示する。
    return excerpt if len(excerpt) <= 420 else excerpt[:417].rsplit(" ", 1)[0] + "…"


@lru_cache(maxsize=64)
def _search_cached(term: str, ranking_terms_key: tuple[str, ...]) -> tuple[Article, ...]:
    search_xml = _request("esearch.fcgi", {"db": "pubmed", "retmode": "xml", "sort": "relevance", "retmax": str(MAX_ARTICLES), "term": term})
    try:
        ids = [node.text for node in ElementTree.fromstring(search_xml).findall(".//Id") if node.text]
    except ElementTree.ParseError as exc:
        raise ResearchUnavailable("PubMed の検索結果を読み取れませんでした。") from exc
    if not ids:
        return ()
    articles_xml = _request("efetch.fcgi", {"db": "pubmed", "retmode": "xml", "id": ",".join(ids)})
    return tuple(_rank(_parse_articles(articles_xml), set(ranking_terms_key)))


def search(question: str) -> dict:
    """検索・取得・再順位付けを行い、出典つきの RAG 結果を返す。"""
    term, ranking_terms = build_search_term(question)
    articles = _search_cached(term, tuple(sorted(ranking_terms)))
    return {
        "question": question,
        "search_term": term,
        "answer": (
            f"PubMed から関連する論文を {len(articles)} 件取得しました。"
            "下の各論文の書誌情報と抄録抜粋を確認し、結論は原著・レビュー本文で検証してください。"
            if articles else
            "関連する論文を取得できませんでした。質問を短くして、サウナ・水風呂・睡眠などの語を含めて再検索してください。"
        ),
        "safety_note": "これは文献検索であり、診断・治療・個別の安全判断ではありません。持病、妊娠中、服薬中、症状がある場合は医療者に相談してください。",
        "articles": [article.public(ranking_terms) for article in articles],
    }
