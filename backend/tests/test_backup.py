import sqlite3

from app import backup, db


def test_backup_copies_data_and_prunes_old(tmp_path, monkeypatch):
    monkeypatch.setenv("SAUNA_DB", str(tmp_path / "live.db"))
    db.init_db()
    with db.connect() as conn:
        db.create_user(conn, "a@example.com", "A", "hash", "v1")

    dest_dir = tmp_path / "bk"
    for i in range(4):
        (dest_dir).mkdir(exist_ok=True)
        (dest_dir / f"sauna-2020010{i}-000000.db").write_bytes(b"old")
    out = backup.run(dest_dir, keep=3)

    copied = sqlite3.connect(out)
    assert copied.execute("SELECT email FROM users").fetchone() == ("a@example.com",)
    copied.close()
    assert len(list(dest_dir.glob("sauna-*.db"))) == 3  # 古い世代は消える
    assert out.exists()


def test_init_db_upgrades_an_old_database(tmp_path, monkeypatch):
    """同意・心拍の列がない古いDBでも、データを残したまま起動できる。"""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, visited_on TEXT NOT NULL,
            facility TEXT NOT NULL DEFAULT '', sauna_temp REAL, water_temp REAL, sleep_hours REAL,
            fatigue TEXT NOT NULL DEFAULT 'mid', ate_before INTEGER NOT NULL DEFAULT 0, score INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '', is_sample INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE sets (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL, set_no INTEGER NOT NULL,
            sauna_min REAL NOT NULL, water_min REAL NOT NULL, rest_min REAL NOT NULL);
        INSERT INTO users (email, display_name, password_hash) VALUES ('old@example.com', '旧', 'h');
        INSERT INTO sessions (user_id, visited_on, score) VALUES (1, '2026-10-01', 4);
        INSERT INTO sets (session_id, set_no, sauna_min, water_min, rest_min) VALUES (1, 1, 9, 1, 10);
        """
    )
    con.commit(); con.close()

    monkeypatch.setenv("SAUNA_DB", str(path))
    db.init_db()
    db.init_db()  # 2回目も壊れない
    with db.connect() as conn:
        user = conn.execute("SELECT consent_version FROM users").fetchone()
        assert user["consent_version"] is None  # 古い利用者は、再同意を求められる
        (s,) = db.fetch_sessions(conn, 1)
        assert s["resting_hr"] is None and s["score"] == 4 and len(s["sets"]) == 1
