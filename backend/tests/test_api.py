import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAUNA_DB", str(tmp_path / "test.db"))
    from app.main import app

    with TestClient(app) as c:  # lifespan を走らせて DB を初期化する
        yield c


def payload(**over):
    body = {
        "visited_on": "2026-10-10",
        "facility": "テスト施設",
        "sauna_temp": 90,
        "water_temp": 15,
        "sleep_hours": 7,
        "fatigue": "mid",
        "ate_before": False,
        "score": 4,
        "note": "",
        "sets": [
            {"sauna_min": 9.5, "water_min": 1.2, "rest_min": 11},
            {"sauna_min": 8.0, "water_min": 1.0, "rest_min": 12},
        ],
    }
    body.update(over)
    return body


def test_create_list_delete(client):
    r = client.post("/api/sessions", json=payload())
    assert r.status_code == 201
    sid = r.json()["id"]

    listed = client.get("/api/sessions").json()
    assert len(listed) == 1
    assert listed[0]["facility"] == "テスト施設"
    assert [s["set_no"] for s in listed[0]["sets"]] == [1, 2]

    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get("/api/sessions").json() == []
    assert client.delete(f"/api/sessions/{sid}").status_code == 404


def test_validation_rejects_bad_input(client):
    assert client.post("/api/sessions", json=payload(score=6)).status_code == 422
    assert client.post("/api/sessions", json=payload(sets=[])).status_code == 422
    assert client.post("/api/sessions", json=payload(water_temp=80)).status_code == 422
    assert client.post("/api/sessions", json=payload(fatigue="extreme")).status_code == 422


def test_sessions_listed_newest_first(client):
    client.post("/api/sessions", json=payload(visited_on="2026-10-01"))
    client.post("/api/sessions", json=payload(visited_on="2026-10-09"))
    dates = [s["visited_on"] for s in client.get("/api/sessions").json()]
    assert dates == ["2026-10-09", "2026-10-01"]


def test_golden_and_dashboard(client):
    client.post("/api/sessions", json=payload(score=5))
    g = client.get("/api/analysis/golden").json()
    assert g["overall"]["n_sessions"] == 1
    assert g["overall"]["sauna_min"] == 8.8  # (9.5 + 8.0) / 2 = 8.75 → 8.8
    d = client.get("/api/analysis/dashboard").json()
    assert d["total_sessions"] == 1 and d["total_sets"] == 2


def test_predict_needs_enough_data(client):
    client.post("/api/sessions", json=payload())
    r = client.post("/api/predict", json={}).json()
    assert r["status"] == "insufficient_data"
    assert r["n_sessions"] == 1


def test_sample_lifecycle_and_predict(client):
    client.post("/api/sessions", json=payload())
    assert client.post("/api/sample?n=40").json() == {"added": 40}
    assert len(client.get("/api/sessions").json()) == 41
    # サンプルを除外すると自分の記録 1 件だけ
    assert len(client.get("/api/sessions?include_sample=false").json()) == 1

    r = client.post(
        "/api/predict",
        json={"water_temp": 14, "sleep_hours": 7, "plan": {"sauna_min": 9.5, "water_min": 1.3, "rest_min": 11}},
    ).json()
    assert r["status"] == "ok"
    assert r["best"]["predicted_score"] >= 1
    assert "plan" in r and r["metrics"]["n_sessions"] == 41

    assert client.delete("/api/sample").json() == {"deleted": 40}
    assert len(client.get("/api/sessions").json()) == 1


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "ととのい黄金比" in r.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
