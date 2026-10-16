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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "user_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON sessions (user_id, visited_on)")


def create_user(conn: sqlite3.Connection, email: str, display_name: str, password_hash: str) -> dict:
    cur = conn.execute(
        "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
        (email, display_name, password_hash),
    )
    return {"id": int(cur.lastrowid), "email": email, "display_name": display_name}


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
            (user_id, visited_on, facility, sauna_temp, water_temp, sleep_hours,
             fatigue, ate_before, score, note, is_sample)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            str(data["visited_on"]),
            data.get("facility", ""),
            data.get("sauna_temp"),
            data.get("water_temp"),
            data.get("sleep_hours"),
            data.get("fatigue", "mid"),
            1 if data.get("ate_before") else 0,
            data["score"],
            data.get("note", ""),
            1 if is_sample else 0,
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
            "fatigue": r["fatigue"],
            "ate_before": bool(r["ate_before"]),
            "score": r["score"],
            "note": r["note"],
            "is_sample": bool(r["is_sample"]),
            "sets": sets_by_session.get(r["id"], []),
        }
        for r in rows
    ]


def delete_session(conn: sqlite3.Connection, session_id: int, user_id: int) -> bool:
    cur = conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
    return cur.rowcount > 0


def delete_samples(conn: sqlite3.Connection, user_id: int) -> int:
    cur = conn.execute("DELETE FROM sessions WHERE is_sample = 1 AND user_id = ?", (user_id,))
    return cur.rowcount
