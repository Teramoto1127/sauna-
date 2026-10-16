import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAUNA_DB", str(tmp_path / "test.db"))
    from app.main import app

    with TestClient(app) as c:  # lifespan を走らせて DB を初期化する
        registered = c.post(
            "/api/auth/register",
            json={"email": "tester@example.com", "display_name": "テスト利用者", "password": "correct-horse-42"},
        )
        assert registered.status_code == 201
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


def test_auth_lifecycle_and_access_control(client):
    assert client.get("/api/auth/me").json()["email"] == "tester@example.com"
    assert client.post("/api/auth/register", json={
        "email": "tester@example.com", "display_name": "重複", "password": "correct-horse-42",
    }).status_code == 409

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/sessions").status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "tester@example.com", "password": "wrong-password",
    }).status_code == 401

    login = client.post("/api/auth/login", json={
        "email": "TESTER@example.com", "password": "correct-horse-42",
    })
    assert login.status_code == 200
    assert login.json()["display_name"] == "テスト利用者"


def test_sessions_are_isolated_between_users(client):
    sid = client.post("/api/sessions", json=payload()).json()["id"]
    registered = client.post("/api/auth/register", json={
        "email": "other@example.com", "display_name": "別の利用者", "password": "different-password-7",
    })
    assert registered.status_code == 201
    assert client.get("/api/sessions").json() == []
    assert client.delete(f"/api/sessions/{sid}").status_code == 404


def test_first_registration_claims_legacy_records(tmp_path, monkeypatch):
    """認証導入前の sessions テーブルを消さずに最初の利用者へ移せる。"""
    legacy_db = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visited_on TEXT NOT NULL,
                facility TEXT NOT NULL DEFAULT '', sauna_temp REAL, water_temp REAL, sleep_hours REAL,
                fatigue TEXT NOT NULL DEFAULT 'mid', ate_before INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', is_sample INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
                set_no INTEGER NOT NULL, sauna_min REAL NOT NULL, water_min REAL NOT NULL, rest_min REAL NOT NULL
            );
            INSERT INTO sessions (visited_on, score) VALUES ('2026-10-01', 4);
            INSERT INTO sets (session_id, set_no, sauna_min, water_min, rest_min) VALUES (1, 1, 9, 1, 10);
        """)
    monkeypatch.setenv("SAUNA_DB", str(legacy_db))
    from app.main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/register", json={
            "email": "legacy@example.com", "display_name": "既存利用者", "password": "legacy-password-9",
        })
        assert r.status_code == 201
        assert r.json()["claimed_legacy_sessions"] == 1
        assert len(c.get("/api/sessions").json()) == 1


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
    assert "ログイン" in r.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
