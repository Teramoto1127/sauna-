"""ローカルアプリ向けのパスワードハッシュとセッションCookieの処理。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException

from . import db


COOKIE_NAME = "sauna_session"
# 同意画面(privacy.html)の文面を変えたら、この版も更新する。古い版の利用者には再同意を求める。
CONSENT_VERSION = "2026-10-23"
SESSION_DAYS = 30
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 64


def hash_password(password: str) -> str:
    """ランダムsalt付きのscryptハッシュを保存可能な文字列にする。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DKLEN)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt.encode("ascii")),
            n=int(n), r=int(r), p=int(p), dklen=DKLEN,
        )
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(expected.encode("ascii")))
    except (ValueError, TypeError, UnicodeError):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(conn, user_id: int) -> str:
    """生トークンは一度だけCookieへ渡し、DBにはSHA-256のみを保存する。"""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (_now() + timedelta(days=SESSION_DAYS)).isoformat()
    conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (_now().isoformat(),))
    conn.execute(
        "INSERT INTO auth_sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
        (user_id, token_hash, expires_at),
    )
    return token


def remove_session(conn, token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))


def current_user(token: str | None) -> dict | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT users.id, users.email, users.display_name, users.consent_version
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_sessions.expires_at > ?
            """,
            (token_hash, _now().isoformat()),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "consent_current": row["consent_version"] == CONSENT_VERSION,
    }


def remove_other_sessions(conn, user_id: int, keep_token: str | None) -> None:
    """パスワード変更時に、いま使っている端末以外のログインを無効にする。"""
    keep = hashlib.sha256(keep_token.encode("utf-8")).hexdigest() if keep_token else ""
    conn.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?", (user_id, keep))


def require_user(token: str | None) -> dict:
    user = current_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    return user
