"""検索モデルのテスト。

使う論文データは、動作確認のために作った【架空の文書】で、実在の論文ではない。
PMID も実在しない番号(9000001〜)にしてある。
"""

import json

import pytest

from app import evaluate, retrieval
from .test_api import client  # noqa: F401  (fixture)

FAKE_DOCS = [
    {"pmid": "9000001", "title": "Sauna bathing and blood pressure in adults (test fixture)",
     "abstract": "Regular sauna bathing was associated with lower blood pressure and reduced hypertension risk."},
    {"pmid": "9000002", "title": "Sleep quality after warm bathing (test fixture)",
     "abstract": "Warm bathing before bedtime shortened sleep onset and improved sleep efficiency."},
    {"pmid": "9000003", "title": "Cold water immersion and the cold shock response (test fixture)",
     "abstract": "Sudden cold water immersion causes gasping, hyperventilation and arrhythmia; habituation reduces the response."},
    {"pmid": "9000004", "title": "Alcohol use during sauna bathing (test fixture)",
     "abstract": "Alcohol consumption during sauna bathing increases the risk of hypotension and arrhythmia."},
    {"pmid": "9000005", "title": "Electrolyte loss through sweat (test fixture)",
     "abstract": "Sweat loss in hot environments removes sodium and potassium and causes dehydration."},
    {"pmid": "9000006", "title": "A study of gardening habits (test fixture)",
     "abstract": "Gardening frequency and neighbourhood green space were surveyed in a small town."},
]


@pytest.fixture()
def corpus_file(tmp_path, monkeypatch):
    path = tmp_path / "corpus.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for d in FAKE_DOCS:
            f.write(json.dumps({**d, "journal": "Test Journal", "published": "2024", "authors": "A. Tester",
                                "publication_types": ["Test"]}, ensure_ascii=False) + "\n")
    monkeypatch.setenv("SAUNA_CORPUS", str(path))
    retrieval._cache.clear()
    return path


def top_pmid(question):
    return retrieval.search(question)["articles"][0]["pmid"]


def test_japanese_questions_find_matching_documents(corpus_file):
    assert top_pmid("サウナは血圧にどのような影響がありますか？") == "9000001"
    assert top_pmid("サウナと睡眠の質の関係を知りたい") == "9000002"
    assert top_pmid("水風呂に入ると寒冷ショックが起きますか") == "9000003"
    assert top_pmid("サウナ中の飲酒は危険ですか？") == "9000004"
    assert top_pmid("サウナの発汗で失われる電解質は？") == "9000005"


def test_unrelated_document_is_not_returned(corpus_file):
    pmids = [a["pmid"] for a in retrieval.search("サウナと血圧", k=10)["articles"]]
    assert "9000006" not in pmids


def test_result_has_citation_and_link(corpus_file):
    a = retrieval.search("サウナと血圧")["articles"][0]
    assert a["url"] == "https://pubmed.ncbi.nlm.nih.gov/9000001/"
    assert a["journal"] == "Test Journal"
    assert a["score"] > 0
    assert len(a["abstract_excerpt"]) <= 420


def test_question_with_no_known_topic_returns_nothing(corpus_file):
    assert retrieval.query_to_english("今日の天気は") == []
    result = retrieval.search("今日の天気は")
    assert result["articles"] == []
    assert "見つかりませんでした" in result["answer"]


def test_english_words_in_question_are_used(corpus_file):
    assert top_pmid("alcohol and sauna") == "9000004"


def test_missing_corpus_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SAUNA_CORPUS", str(tmp_path / "none.jsonl"))
    retrieval._cache.clear()
    with pytest.raises(retrieval.CorpusMissing) as e:
        retrieval.search("サウナ")
    assert "npm run corpus" in str(e.value)


def test_stemming_matches_word_variants():
    assert retrieval.stem("bathing") == retrieval.stem("bath") == "bath"
    assert retrieval.stem("heating") == "heat"
    assert retrieval.stem("studies") == "study"
    assert retrieval.stem("sauna") == "sauna"  # 変化させない語はそのまま
    assert retrieval.analyze("The Sauna Bathing") == ["sauna", "bath"]  # 冠詞は除かれる


# ---- 評価指標 ---------------------------------------------------------------


def test_metrics_known_values():
    ranked = ["a", "b", "c", "d", "e", "f"]
    assert evaluate.hit_at_1(ranked, {"a"}) == 1.0
    assert evaluate.hit_at_1(ranked, {"b"}) == 0.0
    assert evaluate.recall_at_k(ranked, {"b", "z"}, 5) == 0.5
    assert evaluate.recall_at_k(ranked, {"f"}, 5) == 0.0  # 6位は上位5件に入らない
    assert evaluate.reciprocal_rank(ranked, {"c"}) == pytest.approx(1 / 3)
    assert evaluate.reciprocal_rank(ranked, {"zzz"}) == 0.0
    assert evaluate.recall_at_k(ranked, set()) == 0.0


def test_evaluate_skips_unlabeled_and_compares_methods(corpus_file):
    index = retrieval.get_index()
    queries = [
        {"id": "t1", "question": "サウナは血圧にどのような影響がありますか？", "relevant_pmids": ["9000001"]},
        {"id": "t2", "question": "サウナと睡眠の質の関係を知りたい", "relevant_pmids": ["9000002"]},
        {"id": "t3", "question": "ラベルなしの質問", "relevant_pmids": []},
    ]
    result = evaluate.evaluate(index, queries)
    assert result["n_labeled"] == 2 and result["n_unlabeled"] == 1
    assert result["average"]["tfidf"]["hit@1"] == 1.0
    assert set(result["average"]) == {"keyword", "tfidf"}


def test_shipped_eval_queries_are_valid_and_unlabeled():
    """配布する質問ファイルは、正解を勝手に付けていない(チームが付ける)。"""
    queries = evaluate.load_queries(evaluate.DEFAULT_QUERIES)
    assert len(queries) >= 10
    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids))
    assert all(q["question"].strip() for q in queries)


# ---- API --------------------------------------------------------------------


def test_local_research_endpoint(client, corpus_file):  # noqa: F811
    r = client.post("/api/research/local", json={"question": "サウナと血圧について"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "tfidf-local"
    assert body["corpus_size"] == len(FAKE_DOCS)
    assert body["articles"][0]["pmid"] == "9000001"
    assert "診断" in body["safety_note"]


def test_local_research_without_corpus_is_503(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SAUNA_CORPUS", str(tmp_path / "none.jsonl"))
    retrieval._cache.clear()
    r = client.post("/api/research/local", json={"question": "サウナと血圧について"})
    assert r.status_code == 503
    assert "npm run corpus" in r.json()["detail"]


def test_local_research_requires_login(client, corpus_file):  # noqa: F811
    client.post("/api/auth/logout")
    assert client.post("/api/research/local", json={"question": "サウナと血圧"}).status_code == 401
