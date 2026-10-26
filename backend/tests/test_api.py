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
            json={"email": "tester@example.com", "display_name": "テスト利用者", "password": "correct-horse-42", "agree": True},
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
        "email": "tester@example.com", "display_name": "重複", "password": "correct-horse-42", "agree": True,
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
        "email": "other@example.com", "display_name": "別の利用者", "password": "different-password-7", "agree": True,
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
            "email": "legacy@example.com", "display_name": "既存利用者", "password": "legacy-password-9", "agree": True,
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
    assert 'id="logout-button"' in r.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/auth.js").status_code == 200
    login = client.get("/login.html")
    assert login.status_code == 200 and 'id="login-form"' in login.text
    register = client.get("/register.html")
    assert register.status_code == 200 and 'id="register-form"' in register.text
    assert client.get("/api/health").json() == {"status": "ok"}


# ---- 入力ミスの警告と編集 -----------------------------------------------------


def test_implausible_values_need_confirmation(client):
    bad = payload(sets=[{"sauna_min": 95, "water_min": 1, "rest_min": 10}])  # 95は範囲外(422)
    assert client.post("/api/sessions", json=bad).status_code == 422

    odd = payload(water_temp=30, sets=[{"sauna_min": 9, "water_min": 12, "rest_min": 10}])
    r = client.post("/api/sessions", json=odd)
    assert r.status_code == 409
    warnings = r.json()["detail"]["warnings"]
    assert any("水風呂の温度" in w for w in warnings)
    assert any("水風呂" in w and "長すぎ" in w for w in warnings)
    assert client.get("/api/sessions").json() == []  # 保存されていない

    ok = client.post("/api/sessions?confirmed=true", json=odd)
    assert ok.status_code == 201
    assert len(client.get("/api/sessions").json()) == 1


def test_normal_values_have_no_warnings(client):
    assert client.post("/api/sessions", json=payload()).status_code == 201


def test_future_date_is_rejected(client):
    assert client.post("/api/sessions?confirmed=true", json=payload(visited_on="2099-01-01")).status_code == 422


def test_update_session_replaces_sets(client):
    sid = client.post("/api/sessions", json=payload()).json()["id"]
    changed = payload(score=2, facility="別の施設", sets=[{"sauna_min": 7, "water_min": 1, "rest_min": 9}])
    assert client.put(f"/api/sessions/{sid}", json=changed).status_code == 200
    (s,) = client.get("/api/sessions").json()
    assert s["score"] == 2 and s["facility"] == "別の施設"
    assert s["sets"] == [{"set_no": 1, "sauna_min": 7.0, "water_min": 1.0, "rest_min": 9.0}]


def test_update_checks_warnings_and_missing(client):
    sid = client.post("/api/sessions", json=payload()).json()["id"]
    assert client.put(f"/api/sessions/{sid}", json=payload(sleep_hours=0.5)).status_code == 409
    assert client.get("/api/sessions").json()[0]["sleep_hours"] == 7  # 変わっていない
    assert client.put("/api/sessions/9999", json=payload()).status_code == 404


def test_cannot_update_other_users_session(client):
    sid = client.post("/api/sessions", json=payload()).json()["id"]
    client.post("/api/auth/logout")
    other = client.post("/api/auth/register", json={
        "email": "other@example.com", "display_name": "別の人", "password": "another-pass-77", "agree": True})
    assert other.status_code == 201
    assert client.put(f"/api/sessions/{sid}", json=payload(score=1)).status_code == 404


# ---- CSV ---------------------------------------------------------------------


def test_csv_export_roundtrip_and_formula_guard(client):
    client.post("/api/sessions", json=payload(facility="=SUM(A1)", note="+メモ"))
    client.post("/api/sessions", json=payload(visited_on="2026-10-12", score=5))
    r = client.get("/api/export/sessions.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("record_no,visited_on")
    assert "'=SUM(A1)" in text and "'+メモ" in text  # 式として実行されない
    assert len(text.splitlines()) == 1 + 4  # ヘッダー + 2記録 x 2セット

    # 別のアカウントへ取り込める(式ガードは取り除かれる)
    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"email": "b@example.com", "display_name": "B", "password": "another-pass-77", "agree": True})
    check = client.post("/api/import/sessions", content=r.content).json()
    assert check == {"committed": False, "importable": 2, "imported": 0, "duplicates": 0, "errors": [], "warnings": []}
    assert client.get("/api/sessions").json() == []  # 確認だけでは保存されない
    done = client.post("/api/import/sessions?commit=true", content=r.content).json()
    assert done["imported"] == 2
    listed = {s["visited_on"]: s for s in client.get("/api/sessions").json()}
    assert listed["2026-10-10"]["facility"] == "=SUM(A1)" and listed["2026-10-10"]["note"] == "+メモ"
    assert len(listed["2026-10-10"]["sets"]) == 2
    again = client.post("/api/import/sessions?commit=true", content=r.content).json()
    assert again["imported"] == 0 and again["duplicates"] == 2  # 二重取り込みしない


def test_csv_export_excludes_samples_by_default(client):
    client.post("/api/sample?n=5")
    client.post("/api/sessions", json=payload())
    body = client.get("/api/export/sessions.csv").content.decode("utf-8-sig")
    assert len(body.splitlines()) == 1 + 2
    assert len(client.get("/api/export/sessions.csv?include_sample=true").content.decode("utf-8-sig").splitlines()) > 3


def test_csv_import_reports_bad_rows_and_warnings(client):
    head = "record_no,visited_on,facility,sauna_temp,water_temp,sleep_hours,fatigue,ate_before,score,note,set_no,sauna_min,water_min,rest_min\n"
    rows = (
        "1,2026-10-01,A,90,15,7,mid,no,4,,1,9,1.5,10\n"
        "2,2026-13-45,A,90,15,7,mid,no,4,,1,9,1.5,10\n"        # 日付が不正
        "3,2026-10-02,A,90,15,7,mid,no,9,,1,9,1.5,10\n"        # 点数が範囲外
        "4,2026-10-03,A,90,30,7,mid,no,4,,1,9,12,10\n"         # 警告(水風呂温度・水風呂長すぎ)
        "5,2026-10-04,A,90,15,7,mid,no,4,,1,abc,1,10\n"        # 数値でない
    )
    r = client.post("/api/import/sessions", content=(head + rows).encode()).json()
    assert r["importable"] == 2
    assert sorted(e["record"] for e in r["errors"]) == ["2", "3", "5"]
    assert [w["record"] for w in r["warnings"]] == ["4"]
    assert client.post("/api/import/sessions", content=b"a,b\n1,2\n").status_code == 400
    assert client.post("/api/import/sessions", content="あ".encode("shift_jis")).status_code == 400
    assert client.get("/api/sessions").json() == []


# ---- 同意・パスワード変更・削除 ----------------------------------------------------


def test_register_requires_consent(client):
    client.post("/api/auth/logout")
    base = {"email": "c@example.com", "display_name": "C", "password": "another-pass-77"}
    assert client.post("/api/auth/register", json=base).status_code == 422
    assert client.post("/api/auth/register", json={**base, "agree": False}).status_code == 422
    ok = client.post("/api/auth/register", json={**base, "agree": True})
    assert ok.status_code == 201 and ok.json()["consent_current"] is True
    assert client.get("/api/auth/me").json()["consent_current"] is True


def test_old_consent_version_is_flagged_and_can_be_renewed(client):
    import sqlite3 as _sqlite3
    from app import db as _db

    conn = _sqlite3.connect(_db.db_path())
    conn.execute("UPDATE users SET consent_version = '2000-01-01'")
    conn.commit(); conn.close()
    assert client.get("/api/auth/me").json()["consent_current"] is False
    assert client.post("/api/account/consent", json={"agree": False}).status_code == 422
    assert client.post("/api/account/consent", json={"agree": True}).status_code == 200
    assert client.get("/api/auth/me").json()["consent_current"] is True


def test_change_password(client):
    assert client.post("/api/account/password", json={"current_password": "wrong-password", "new_password": "new-password-99"}).status_code == 403
    assert client.post("/api/account/password", json={"current_password": "correct-horse-42", "new_password": "short"}).status_code == 422
    assert client.post("/api/account/password", json={"current_password": "correct-horse-42", "new_password": "new-password-99"}).status_code == 204
    assert client.get("/api/auth/me").status_code == 200  # この端末はログインしたまま
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "tester@example.com", "password": "correct-horse-42"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "tester@example.com", "password": "new-password-99"}).status_code == 200


def test_delete_my_records_keeps_account(client):
    client.post("/api/sessions", json=payload())
    assert client.post("/api/account/delete-data", json={"password": "nope"}).status_code == 403
    assert len(client.get("/api/sessions").json()) == 1
    assert client.post("/api/account/delete-data", json={"password": "correct-horse-42"}).json() == {"deleted": 1}
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/auth/me").status_code == 200


def test_delete_account_removes_everything(client):
    from app import db as _db

    client.post("/api/sessions", json=payload())
    assert client.post("/api/account/delete", json={"password": "nope"}).status_code == 403
    assert client.post("/api/account/delete", json={"password": "correct-horse-42"}).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    with _db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
    assert client.post("/api/auth/login", json={"email": "tester@example.com", "password": "correct-horse-42"}).status_code == 401


# ---- 予測の注意・答え合わせ・心拍 -----------------------------------------------------


def test_predict_is_blocked_on_risky_days(client):
    for flag in ("drank", "unwell", "restricted"):
        r = client.post("/api/predict", json={flag: True}).json()
        assert r["status"] == "blocked" and len(r["reasons"]) == 1
    both = client.post("/api/predict", json={"drank": True, "unwell": True}).json()
    assert len(both["reasons"]) == 2
    assert "best" not in both  # サウナを勧める形の提案は出さない


def test_prediction_review_pairs_prediction_with_actual(client):
    client.post("/api/sample?n=60")
    ctx = {"sauna_temp": 90, "water_temp": 15, "sleep_hours": 7, "n_sets": 3, "for_date": "2026-10-20"}
    r = client.post("/api/predict", json=ctx).json()
    assert r["status"] == "ok"
    client.post("/api/predict", json=ctx)  # 同じ日・同じ条件は1件に置き換わる
    review = client.get("/api/predictions/review").json()
    assert review["summary"]["n_predictions"] == 1 and review["summary"]["n_matched"] == 0
    assert review["items"][0]["actual"] is None
    assert review["items"][0]["plan"]["sauna_min"] == r["best"]["sauna_min"]

    client.post("/api/sessions", json=payload(visited_on="2026-10-20", score=5))
    review = client.get("/api/predictions/review").json()
    item = review["items"][0]
    assert item["actual"]["score"] == 5
    assert item["error"] == round(5 - r["best"]["predicted_score"], 2)
    assert review["summary"]["mean_abs_error"] is None  # 3件未満では平均誤差を出さない


def test_review_summary_with_enough_matches():
    from app import analysis

    preds = [{"for_date": f"2026-10-0{i}", "predicted_score": 4.0, "sauna_min": 9, "water_min": 1, "rest_min": 10} for i in (1, 2, 3)]
    sessions = [
        {"id": i, "visited_on": f"2026-10-0{i}", "facility": "", "score": sc, "sets": [{"set_no": 1, "sauna_min": 8, "water_min": 1, "rest_min": 9}]}
        for i, sc in ((1, 3), (2, 5), (3, 4))
    ]
    s = analysis.review_predictions(preds, sessions)["summary"]
    assert s["n_matched"] == 3 and s["mean_abs_error"] == 0.67 and s["mean_error"] == 0.0


def test_resting_hr_is_saved_checked_and_exported(client):
    assert client.post("/api/sessions", json=payload(resting_hr=10)).status_code == 422
    r = client.post("/api/sessions", json=payload(resting_hr=120))
    assert r.status_code == 409 and any("安静時心拍" in w for w in r.json()["detail"]["warnings"])
    assert client.post("/api/sessions", json=payload(resting_hr=58)).status_code == 201
    assert client.get("/api/sessions").json()[0]["resting_hr"] == 58
    csv_text = client.get("/api/export/sessions.csv").content.decode("utf-8-sig")
    assert "resting_hr" in csv_text.splitlines()[0] and ",58.0," in csv_text


def test_account_deletion_removes_predictions(client):
    from app import db as _db

    client.post("/api/sample?n=60")
    client.post("/api/predict", json={"for_date": "2026-10-20"})
    client.post("/api/account/delete", json={"password": "correct-horse-42"})
    with _db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0


def test_privacy_page_is_served(client):
    r = client.get("/privacy.html")
    assert r.status_code == 200 and "データの扱い" in r.text


def test_pwa_files_are_served(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"] == "/"
    assert client.get("/icon.svg").status_code == 200


# ---- 位置情報 --------------------------------------------------------------------


@pytest.fixture()
def no_external(monkeypatch):
    monkeypatch.setenv("SAUNA_DISABLE_EXTERNAL_PLACES", "1")


def test_location_is_rounded_paired_and_optional(client):
    assert client.post("/api/sessions", json=payload(lat=35.68)).status_code == 422  # 片方だけは不可
    assert client.post("/api/sessions", json=payload(lat=95, lng=139)).status_code == 422
    sid = client.post("/api/sessions", json=payload(lat=35.681234, lng=139.767891)).json()["id"]
    s = client.get("/api/sessions").json()[0]
    assert (s["lat"], s["lng"]) == (35.681, 139.768)  # 約110mに丸めて保存
    # 位置なしで編集すると位置は消える(画面は、編集時に元の位置を送り直す)
    client.put(f"/api/sessions/{sid}", json=payload())
    assert client.get("/api/sessions").json()[0]["lat"] is None


def test_nearby_uses_own_records_and_not_other_users(client, no_external):
    client.post("/api/sessions", json=payload(facility="北の湯", lat=35.6801, lng=139.7601))
    r = client.get("/api/facilities/nearby?lat=35.6802&lng=139.7602").json()
    assert [k["facility"] for k in r["known"]] == ["北の湯"]
    assert r["suggested"] == [] and r["suggest_available"] is False
    assert client.get("/api/facilities/nearby?lat=36.5&lng=139.7602").json()["known"] == []

    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"email": "z@example.com", "display_name": "Z", "password": "another-pass-77", "agree": True})
    assert client.get("/api/facilities/nearby?lat=35.6802&lng=139.7602").json()["known"] == []  # 他人の場所は見えない


def test_nearby_requires_login_and_valid_coordinates(client):
    assert client.get("/api/facilities/nearby?lat=200&lng=0").status_code == 422
    client.post("/api/auth/logout")
    assert client.get("/api/facilities/nearby?lat=35&lng=139").status_code == 401


def test_nearby_suggestions_exclude_names_already_known(client, monkeypatch):
    from app import places as _places

    client.post("/api/sessions", json=payload(facility="北の湯", lat=35.6801, lng=139.7601))
    monkeypatch.setattr(_places, "external_nearby", lambda lat, lng: {
        "available": True, "places": [{"name": "北の湯", "distance_m": 10}, {"name": "新しい湯", "distance_m": 200}]})
    r = client.get("/api/facilities/nearby?lat=35.6801&lng=139.7601").json()
    assert [p["name"] for p in r["suggested"]] == ["新しい湯"] and r["suggest_available"] is True


def test_clear_locations_keeps_records(client):
    client.post("/api/sessions", json=payload(lat=35.681, lng=139.767))
    assert client.post("/api/account/clear-locations", json={"password": "nope"}).status_code == 403
    assert client.post("/api/account/clear-locations", json={"password": "correct-horse-42"}).json() == {"cleared": 1}
    (s,) = client.get("/api/sessions").json()
    assert s["lat"] is None and s["facility"] == "テスト施設"


def test_location_is_not_in_csv_export(client):
    client.post("/api/sessions", json=payload(lat=35.681, lng=139.767))
    text = client.get("/api/export/sessions.csv").content.decode("utf-8-sig")
    assert "35.681" not in text and "lat" not in text.splitlines()[0]


def test_nearby_without_external_never_calls_the_map_service(client, monkeypatch):
    from app import places as _places

    def boom(lat, lng):
        raise AssertionError("外部へ問い合わせてはいけない")

    monkeypatch.setattr(_places, "external_nearby", boom)
    client.post("/api/sessions", json=payload(facility="北の湯", lat=35.6801, lng=139.7601))
    r = client.get("/api/facilities/nearby?lat=35.6801&lng=139.7601&external=false").json()
    assert [k["facility"] for k in r["known"]] == ["北の湯"] and r["suggest_available"] is None
