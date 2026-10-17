"""サウナ記録アプリの API。フロントエンド(../frontend)も同じサーバーから配信する。

起動: backend ディレクトリで  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.staticfiles import StaticFiles

from . import analysis, auth, db, ml, research, retrieval, seed
from .schemas import LoginIn, PredictIn, RegisterIn, ResearchQueryIn, SessionIn

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="ととのい黄金比", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---- 認証 -----------------------------------------------------------------


def _authenticated_user(session_token: str | None = Cookie(default=None, alias=auth.COOKIE_NAME)) -> dict:
    return auth.require_user(session_token)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        # HTTPS で公開する場合に有効化する。localhost 開発ではCookieを送れるよう既定はfalse。
        secure=os.environ.get("SAUNA_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    )


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response) -> dict:
    with db.connect() as conn:
        try:
            user = db.create_user(conn, body.email, body.display_name, auth.hash_password(body.password))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="このメールアドレスはすでに登録されています") from exc
        # 認証導入前は全記録に所有者がなかったため、最初の新規アカウントだけが引き継ぐ。
        user["claimed_legacy_sessions"] = db.claim_legacy_sessions(conn, user["id"])
        token = auth.create_session(conn, user["id"])
    _set_session_cookie(response, token)
    return user


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response) -> dict:
    with db.connect() as conn:
        row = db.fetch_user_by_email(conn, body.email)
        if row is None or not auth.verify_password(body.password, row["password_hash"]):
            # アカウントの有無を分けて返さない。
            raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが違います")
        user = {"id": row["id"], "email": row["email"], "display_name": row["display_name"]}
        token = auth.create_session(conn, user["id"])
    _set_session_cookie(response, token)
    return user


@app.get("/api/auth/me")
def me(user: dict = Depends(_authenticated_user)) -> dict:
    return user


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, session_token: str | None = Cookie(default=None, alias=auth.COOKIE_NAME)) -> None:
    with db.connect() as conn:
        auth.remove_session(conn, session_token)
    response.delete_cookie(key=auth.COOKIE_NAME, httponly=True, samesite="lax")


# ---- セッション記録 -------------------------------------------------------


@app.get("/api/sessions")
def list_sessions(include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> list[dict]:
    """新しい順。"""
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)
    return list(reversed(sessions))


@app.post("/api/sessions", status_code=201)
def create_session(body: SessionIn, user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        session_id = db.insert_session(conn, body.model_dump(mode="json"), user["id"])
    return {"id": session_id}


@app.delete("/api/sessions/{session_id}", status_code=204)
def remove_session(session_id: int, user: dict = Depends(_authenticated_user)) -> None:
    with db.connect() as conn:
        if not db.delete_session(conn, session_id, user["id"]):
            raise HTTPException(status_code=404, detail="記録が見つかりません")


# ---- 分析 -----------------------------------------------------------------


@app.get("/api/analysis/golden")
def golden(include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)
    return analysis.golden_report(sessions)


@app.get("/api/analysis/dashboard")
def dashboard(include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)
    return analysis.dashboard(sessions)


# ---- 予測 -----------------------------------------------------------------


@app.post("/api/predict")
def predict(body: PredictIn, include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)

    model = ml.train(sessions)
    if model is None:
        return {
            "status": "insufficient_data",
            "n_sessions": len(sessions),
            "needed": ml.MIN_SESSIONS,
            "message": f"予測には {ml.MIN_SESSIONS} 件以上の記録が必要です(現在 {len(sessions)} 件)。",
        }

    ctx = body.model_dump()
    plan = ctx.pop("plan")
    result = ml.optimize(model, ctx)
    result.update({"status": "ok", "metrics": model.metrics()})
    if plan is not None:
        result["plan"] = ml.predict_plan(
            model, ctx, plan["sauna_min"], plan["water_min"], plan["rest_min"]
        )
    return result


# ---- 医学論文 RAG ---------------------------------------------------------


@app.post("/api/research")
def research_papers(body: ResearchQueryIn, user: dict = Depends(_authenticated_user)) -> dict:
    """PubMed を検索し、質問に関連する論文と短い根拠抜粋を返す。"""
    try:
        return research.search(body.question.strip())
    except research.ResearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/research/local")
def research_local(body: ResearchQueryIn, user: dict = Depends(_authenticated_user)) -> dict:
    """保存済みの論文(npm run corpus で作る)を、自前の TF-IDF 検索で探す。通信は不要。"""
    try:
        return retrieval.search(body.question.strip())
    except retrieval.CorpusMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---- サンプルデータ -------------------------------------------------------


@app.post("/api/sample", status_code=201)
def add_sample(n: int = Query(default=60, ge=1, le=300), user: dict = Depends(_authenticated_user)) -> dict:
    """動作確認用のサンプル記録を追加する(is_sample=1 で区別され、後で一括削除できる)。"""
    items = seed.generate_sessions(n)
    with db.connect() as conn:
        for item in items:
            db.insert_session(conn, {**item, "visited_on": item["visited_on"].isoformat()}, user["id"], is_sample=True)
    return {"added": len(items)}


@app.delete("/api/sample")
def clear_sample(user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        return {"deleted": db.delete_samples(conn, user["id"])}


# ---- フロントエンド -------------------------------------------------------
# API ルートより後に登録すること(先に登録すると / が API を食ってしまう)。
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
