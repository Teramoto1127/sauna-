from app import research
from .test_api import client


SEARCH_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<eSearchResult><IdList><Id>12345</Id><Id>67890</Id></IdList></eSearchResult>"""

FETCH_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation><PMID>12345</PMID><Article>
      <Journal><JournalIssue><PubDate><Year>2024</Year><Month>Jan</Month></PubDate></JournalIssue><Title>Journal of Sauna Studies</Title></Journal>
      <ArticleTitle>Sauna bathing and sleep: a systematic review</ArticleTitle>
      <Abstract><AbstractText Label="RESULTS">Sauna bathing was associated with improved sleep outcomes in small studies. More randomized trials are needed.</AbstractText></Abstract>
      <AuthorList><Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author></AuthorList>
      <PublicationTypeList><PublicationType>Systematic Review</PublicationType></PublicationTypeList>
    </Article></MedlineCitation>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation><PMID>67890</PMID><Article>
      <Journal><JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue><Title>Heat Journal</Title></Journal>
      <ArticleTitle>Cold water immersion after exercise</ArticleTitle>
      <Abstract><AbstractText>Cold water immersion was studied for recovery after exercise.</AbstractText></Abstract>
      <PublicationTypeList><PublicationType>Clinical Trial</PublicationType></PublicationTypeList>
    </Article></MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>"""


def test_research_endpoint_returns_cited_pubmed_articles(client, monkeypatch):
    research._search_cached.cache_clear()

    def fake_request(path, _params):
        return SEARCH_XML if path == "esearch.fcgi" else FETCH_XML

    monkeypatch.setattr(research, "_request", fake_request)
    response = client.post("/api/research", json={"question": "サウナと睡眠について知りたい"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["articles"]) == 2
    assert body["articles"][0]["pmid"] == "12345"
    assert body["articles"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345/"
    assert "睡眠" in body["safety_note"] or "診断" in body["safety_note"]
    assert "sauna" in body["search_term"].lower()


def test_research_rejects_empty_question(client):
    assert client.post("/api/research", json={"question": ""}).status_code == 422
