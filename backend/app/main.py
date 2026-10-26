"""サウナ記録アプリの API。フロントエンド(../frontend)も同じサーバーから配信する。

起動: backend ディレクトリで  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from datetime import date
from pathlib import Path
import sqlite3

from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles

from . import analysis, auth, checks, csvio, db, places, ml, research, retrieval, seed
from .schemas import (
    ConsentIn,
    LoginIn,
    PasswordChangeIn,
    PasswordConfirmIn,
    PredictIn,
    RegisterIn,
    ResearchQueryIn,
    SessionIn,
)

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
            user = db.create_user(
                conn, body.email, body.display_name, auth.hash_password(body.password), auth.CONSENT_VERSION
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="このメールアドレスはすでに登録されています") from exc
        # 認証導入前は全記録に所有者がなかったため、最初の新規アカウントだけが引き継ぐ。
        user["claimed_legacy_sessions"] = db.claim_legacy_sessions(conn, user["id"])
        token = auth.create_session(conn, user["id"])
    _set_session_cookie(response, token)
    user["consent_current"] = True
    user.pop("consent_version", None)
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


# ---- アカウント -----------------------------------------------------------


def _check_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    row = db.fetch_user_by_id(conn, user_id)
    if row is None or not auth.verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=403, detail="パスワードが違います")


@app.post("/api/account/consent")
def give_consent(body: ConsentIn, user: dict = Depends(_authenticated_user)) -> dict:
    with db.connect() as conn:
        db.set_consent(conn, user["id"], auth.CONSENT_VERSION)
    return {"consent_current": True}


@app.post("/api/account/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChangeIn,
    user: dict = Depends(_authenticated_user),
    session_token: str | None = Cookie(default=None, alias=auth.COOKIE_NAME),
) -> None:
    with db.connect() as conn:
        _check_password(conn, user["id"], body.current_password)
        db.set_password(conn, user["id"], auth.hash_password(body.new_password))
        auth.remove_other_sessions(conn, user["id"], session_token)


@app.post("/api/account/clear-locations")
def clear_my_locations(body: PasswordConfirmIn, user: dict = Depends(_authenticated_user)) -> dict:
    """保存した位置情報だけを消す(記録は残す)。"""
    with db.connect() as conn:
        _check_password(conn, user["id"], body.password)
        return {"cleared": db.clear_locations(conn, user["id"])}


@app.post("/api/account/delete-data")
def delete_my_records(body: PasswordConfirmIn, user: dict = Depends(_authenticated_user)) -> dict:
    """自分の記録だけをすべて消す(アカウントは残す)。"""
    with db.connect() as conn:
        _check_password(conn, user["id"], body.password)
        return {"deleted": db.delete_all_sessions(conn, user["id"])}


@app.post("/api/account/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(body: PasswordConfirmIn, response: Response, user: dict = Depends(_authenticated_user)) -> None:
    """アカウントと、記録・ログイン状態をすべて削除する。取り消せない。"""
    with db.connect() as conn:
        _check_password(conn, user["id"], body.password)
        db.delete_user(conn, user["id"])
    response.delete_cookie(key=auth.COOKIE_NAME, httponly=True, samesite="lax")


# ---- セッション記録 -------------------------------------------------------


@app.get("/api/sessions")
def list_sessions(include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> list[dict]:
    """新しい順。"""
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)
    return list(reversed(sessions))


def _require_confirmation(data: dict, confirmed: bool) -> None:
    """打ち間違いの疑いがあれば 409 で止める。confirmed=true なら通す。"""
    found = checks.warnings_for(data)
    if found and not confirmed:
        raise HTTPException(status_code=409, detail={"warnings": found})


@app.post("/api/sessions", status_code=201)
def create_session(body: SessionIn, confirmed: bool = False, user: dict = Depends(_authenticated_user)) -> dict:
    data = body.model_dump(mode="json")
    _require_confirmation(data, confirmed)
    with db.connect() as conn:
        session_id = db.insert_session(conn, data, user["id"])
    return {"id": session_id}


@app.get("/api/facilities/nearby")
def facilities_nearby(
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    external: bool = True,
    user: dict = Depends(_authenticated_user),
) -> dict:
    """現在地の近くの施設。自分の記録から(known)と、地図データから(suggested)。"""
    lat, lng = places.round_coord(lat), places.round_coord(lng)
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample=False)
    known = places.known_nearby(sessions, lat, lng)
    result = {"known": known, "auto_pick_m": places.AUTO_PICK_M, "suggested": [], "suggest_available": None}
    if external:
        ext = places.external_nearby(lat, lng)
        names = {k["facility"] for k in known}
        result["suggested"] = [p for p in ext["places"] if p["name"] not in names]
        result["suggest_available"] = ext["available"]
    return result


@app.get("/api/export/sessions.csv")
def export_sessions(include_sample: bool = False, user: dict = Depends(_authenticated_user)) -> Response:
    """自分の記録をCSVで返す。サンプル(架空データ)は既定で含めない。"""
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, user["id"], include_sample)
    return Response(
        content=csvio.export_csv(sessions).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="totonoi-sessions.csv"'},
    )


@app.post("/api/import/sessions")
async def import_sessions(request: Request, commit: bool = False, user: dict = Depends(_authenticated_user)) -> dict:
    """CSVの取り込み。commit=false は確認だけ(保存しない)、commit=true で保存する。"""
    raw = await request.body()
    with db.connect() as conn:
        existing = db.fetch_sessions(conn, user["id"], True)
        result = csvio.parse_import(raw, existing)
        if "fatal" in result:
            raise HTTPException(status_code=400, detail=result["fatal"])
        imported = 0
        if commit:
            for data in result["valid"]:
                db.insert_session(conn, data, user["id"])
                imported += 1
    return {
        "committed": commit,
        "importable": len(result["valid"]),
        "imported": imported,
        "duplicates": result["duplicates"],
        "errors": result["errors"],
        "warnings": result["warnings"],
    }


@app.put("/api/sessions/{session_id}")
def update_session(session_id: int, body: SessionIn, confirmed: bool = False, user: dict = Depends(_authenticated_user)) -> dict:
    data = body.model_dump(mode="json")
    _require_confirmation(data, confirmed)
    with db.connect() as conn:
        if not db.update_session(conn, session_id, data, user["id"]):
            raise HTTPException(status_code=404, detail="記録が見つかりません")
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


BLOCK_REASONS = {
    "drank": "お酒を飲んでいる",
    "unwell": "熱があるなど、体調がすぐれない",
    "restricted": "医師からサウナを控えるよう言われている",
}


@app.post("/api/predict")
def predict(body: PredictIn, include_sample: bool = True, user: dict = Depends(_authenticated_user)) -> dict:
    reasons = [text for key, text in BLOCK_REASONS.items() if getattr(body, key)]
    if reasons:
        # 条件に当てはまる日は、サウナを勧める形の予測を出さない。
        return {
            "status": "blocked",
            "reasons": reasons,
            "message": "今日は予測を出しません。サウナは、無理をせず控えるほうがよいかもしれません。"
            "体調に不安があるときや、医師の指示があるときは、その指示を優先してください。",
        }

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

    ctx = body.model_dump(exclude={"drank", "unwell", "restricted", "for_date"})
    plan = ctx.pop("plan")
    result = ml.optimize(model, ctx)
    result.update({"status": "ok", "metrics": model.metrics()})
    if plan is not None:
        result["plan"] = ml.predict_plan(
            model, ctx, plan["sauna_min"], plan["water_min"], plan["rest_min"]
        )
    # 答え合わせ用に、提案と条件を残す(実際の記録と、あとで並べて見る)
    with db.connect() as conn:
        db.log_prediction(conn, user["id"], str(body.for_date or date.today()), ctx, result["best"])
    return result


@app.get("/api/predictions/review")
def review_predictions(user: dict = Depends(_authenticated_user)) -> dict:
    """過去の予測と、同じ日に実際に記録した結果を並べて返す。"""
    with db.connect() as conn:
        preds = db.fetch_predictions(conn, user["id"])
        # 架空のサンプル記録は、「実際の結果」として並べない
        sessions = db.fetch_sessions(conn, user["id"], include_sample=False)
    return analysis.review_predictions(preds, sessions)


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
