"""SQLite の接続とスキーマ、セッション記録の保存・取得。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB = Path(__file__).resolve().parent.parent / "sauna.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT    NOT NULL,
    password_hash TEXT   NOT NULL,
    consent_version TEXT,                      -- 同意した説明文の版(未同意は NULL)
    consented_at TEXT,
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT    NOT NULL UNIQUE,
    expires_at  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    visited_on  TEXT    NOT NULL,              -- YYYY-MM-DD
    facility    TEXT    NOT NULL DEFAULT '',
    sauna_temp  REAL,                          -- サウナ室の温度 (℃)
    water_temp  REAL,                          -- 水風呂の温度 (℃)
    sleep_hours REAL,                          -- 前日の睡眠時間
    resting_hr  REAL,                          -- 安静時心拍(bpm・任意。いまは予測に使わない)
    lat         REAL,                          -- 行った場所(約110mに丸めた緯度。任意)
    lng         REAL,
    fatigue     TEXT    NOT NULL DEFAULT 'mid' CHECK (fatigue IN ('low', 'mid', 'high')),
    ate_before  INTEGER NOT NULL DEFAULT 0,    -- サウナ前に食事をしたか
    score       INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    note        TEXT    NOT NULL DEFAULT '',
    is_sample   INTEGER NOT NULL DEFAULT 0     -- サンプルデータなら 1
);

CREATE TABLE IF NOT EXISTS sets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    set_no     INTEGER NOT NULL,
    sauna_min  REAL    NOT NULL,
    water_min  REAL    NOT NULL,
    rest_min   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    for_date    TEXT    NOT NULL,              -- 予測の対象日 YYYY-MM-DD
    context     TEXT    NOT NULL,              -- 予測したときの条件(JSON)
    context_key TEXT    NOT NULL,              -- 条件の同一判定用
    sauna_min   REAL    NOT NULL,              -- 提案した配分
    water_min   REAL    NOT NULL,
    rest_min    REAL    NOT NULL,
    predicted_score REAL NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, for_date, context_key)
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions (visited_on);
CREATE INDEX IF NOT EXISTS idx_sets_session ON sets (session_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions (token_hash);
"""


def db_path() -> str:
    """環境変数 SAUNA_DB があればそれを使う(テストや別DB用)。"""
    return os.environ.get("SAUNA_DB", str(DEFAULT_DB))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # 認証追加前のDBは sessions.user_id を持たない。記録は削除せず、所有者不明の
        # 既存データとして残す（安全に特定の新規ユーザーへ割り当てることはしない）。
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "consent_version" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN consent_version TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN consented_at TEXT")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "resting_hr" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN resting_hr REAL")
        if "lat" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN lat REAL")
            conn.execute("ALTER TABLE sessions ADD COLUMN lng REAL")
        if "user_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON sessions (user_id, visited_on)")


def create_user(
    conn: sqlite3.Connection, email: str, display_name: str, password_hash: str, consent_version: str | None = None
) -> dict:
    cur = conn.execute(
        """
        INSERT INTO users (email, display_name, password_hash, consent_version, consented_at)
        VALUES (?, ?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)
        """,
        (email, display_name, password_hash, consent_version, consent_version),
    )
    return {"id": int(cur.lastrowid), "email": email, "display_name": display_name, "consent_version": consent_version}


def fetch_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT id, email, display_name, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()


def set_consent(conn: sqlite3.Connection, user_id: int, version: str) -> None:
    conn.execute(
        "UPDATE users SET consent_version = ?, consented_at = CURRENT_TIMESTAMP WHERE id = ?", (version, user_id)
    )


def set_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def delete_all_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    """自分の記録をすべて削除する(サンプルも含む)。予測の履歴も消す。"""
    conn.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
    return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    """アカウントと、紐づく記録・ログイン状態をすべて削除する。"""
    conn.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def fetch_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, email, display_name, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()


def claim_legacy_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    """認証導入前の所有者不明レコードを、最初に作られたアカウントへ一度だけ移す。"""
    cur = conn.execute("UPDATE sessions SET user_id = ? WHERE user_id IS NULL", (user_id,))
    return cur.rowcount


def insert_session(conn: sqlite3.Connection, data: dict, user_id: int, is_sample: bool = False) -> int:
    cur = conn.execute(
        """
        INSERT INTO sessions
            (user_id, visited_on, facility, sauna_temp, water_temp, sleep_hours, resting_hr,
             fatigue, ate_before, score, note, is_sample, lat, lng)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(data["visited_on"]),
            data.get("facility", ""),
            data.get("sauna_temp"),
            data.get("water_temp"),
            data.get("sleep_hours"),
            data.get("resting_hr"),
            data.get("fatigue", "mid"),
            1 if data.get("ate_before") else 0,
            data["score"],
            data.get("note", ""),
            1 if is_sample else 0,
            data.get("lat"),
            data.get("lng"),
        ),
    )
    session_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO sets (session_id, set_no, sauna_min, water_min, rest_min) VALUES (?, ?, ?, ?, ?)",
        [
            (session_id, i, s["sauna_min"], s["water_min"], s["rest_min"])
            for i, s in enumerate(data["sets"], start=1)
        ],
    )
    return int(session_id)


def fetch_sessions(conn: sqlite3.Connection, user_id: int, include_sample: bool = True) -> list[dict]:
    """日付の古い順にセッション(セット付き)を返す。"""
    where = "WHERE user_id = ?" + ("" if include_sample else " AND is_sample = 0")
    rows = conn.execute(
        f"SELECT * FROM sessions {where} ORDER BY visited_on ASC, id ASC", (user_id,)
    ).fetchall()
    if not rows:
        return []

    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    set_rows = conn.execute(
        f"SELECT * FROM sets WHERE session_id IN ({placeholders}) ORDER BY session_id, set_no",
        ids,
    ).fetchall()
    sets_by_session: dict[int, list[dict]] = {}
    for s in set_rows:
        sets_by_session.setdefault(s["session_id"], []).append(
            {
                "set_no": s["set_no"],
                "sauna_min": s["sauna_min"],
                "water_min": s["water_min"],
                "rest_min": s["rest_min"],
            }
        )

    return [
        {
            "id": r["id"],
            "visited_on": r["visited_on"],
            "facility": r["facility"],
            "sauna_temp": r["sauna_temp"],
            "water_temp": r["water_temp"],
            "sleep_hours": r["sleep_hours"],
            "resting_hr": r["resting_hr"],
            "lat": r["lat"],
            "lng": r["lng"],
            "fatigue": r["fatigue"],
            "ate_before": bool(r["ate_before"]),
            "score": r["score"],
            "note": r["note"],
            "is_sample": bool(r["is_sample"]),
            "sets": sets_by_session.get(r["id"], []),
        }
        for r in rows
    ]


def update_session(conn: sqlite3.Connection, session_id: int, data: dict, user_id: int) -> bool:
    """自分の記録を丸ごと置き換える(セットも作り直す)。is_sample は変えない。"""
    cur = conn.execute(
        """
        UPDATE sessions SET visited_on = ?, facility = ?, sauna_temp = ?, water_temp = ?,
               sleep_hours = ?, resting_hr = ?, fatigue = ?, ate_before = ?, score = ?, note = ?,
               lat = ?, lng = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            str(data["visited_on"]),
            data.get("facility", ""),
            data.get("sauna_temp"),
            data.get("water_temp"),
            data.get("sleep_hours"),
            data.get("resting_hr"),
            data.get("fatigue", "mid"),
            1 if data.get("ate_before") else 0,
            data["score"],
            data.get("note", ""),
            data.get("lat"),
            data.get("lng"),
            session_id,
            user_id,
        ),
    )
    if cur.rowcount == 0:
        return False
    conn.execute("DELETE FROM sets WHERE session_id = ?", (session_id,))
    conn.executemany(
        "INSERT INTO sets (session_id, set_no, sauna_min, water_min, rest_min) VALUES (?, ?, ?, ?, ?)",
        [
            (session_id, i, x["sauna_min"], x["water_min"], x["rest_min"])
            for i, x in enumerate(data["sets"], start=1)
        ],
    )
    return True


def log_prediction(conn: sqlite3.Connection, user_id: int, for_date: str, ctx: dict, best: dict) -> None:
    """予測を1件記録する。同じ日・同じ条件の予測は、最新のもので置き換える。"""
    import hashlib
    import json

    context = json.dumps(ctx, sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]
    conn.execute(
        """
        INSERT INTO predictions (user_id, for_date, context, context_key, sauna_min, water_min, rest_min, predicted_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, for_date, context_key) DO UPDATE SET
            sauna_min = excluded.sauna_min, water_min = excluded.water_min, rest_min = excluded.rest_min,
            predicted_score = excluded.predicted_score, created_at = CURRENT_TIMESTAMP
        """,
        (user_id, for_date, context, key, best["sauna_min"], best["water_min"], best["rest_min"], best["predicted_score"]),
    )


def fetch_predictions(conn: sqlite3.Connection, user_id: int, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM predictions WHERE user_id = ? ORDER BY for_date DESC, id DESC LIMIT ?", (user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def clear_locations(conn: sqlite3.Connection, user_id: int) -> int:
    """保存した位置情報だけを消す(記録そのものは残す)。"""
    return conn.execute(
        "UPDATE sessions SET lat = NULL, lng = NULL WHERE user_id = ? AND lat IS NOT NULL", (user_id,)
    ).rowcount


def delete_session(conn: sqlite3.Connection, session_id: int, user_id: int) -> bool:
    cur = conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    return cur.rowcount > 0


def delete_samples(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE is_sample = 1 AND user_id = ?", (user_id,))
    return cur.rowcount
