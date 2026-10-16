"""サウナ記録アプリの API。フロントエンド(../frontend)も同じサーバーから配信する。

起動: backend ディレクトリで  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import analysis, db, ml, seed
from .schemas import PredictIn, SessionIn

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="ととのい黄金比", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---- セッション記録 -------------------------------------------------------


@app.get("/api/sessions")
def list_sessions(include_sample: bool = True) -> list[dict]:
    """新しい順。"""
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, include_sample)
    return list(reversed(sessions))


@app.post("/api/sessions", status_code=201)
def create_session(body: SessionIn) -> dict:
    with db.connect() as conn:
        session_id = db.insert_session(conn, body.model_dump(mode="json"))
    return {"id": session_id}


@app.delete("/api/sessions/{session_id}", status_code=204)
def remove_session(session_id: int) -> None:
    with db.connect() as conn:
        if not db.delete_session(conn, session_id):
            raise HTTPException(status_code=404, detail="記録が見つかりません")


# ---- 分析 -----------------------------------------------------------------


@app.get("/api/analysis/golden")
def golden(include_sample: bool = True) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, include_sample)
    return analysis.golden_report(sessions)


@app.get("/api/analysis/dashboard")
def dashboard(include_sample: bool = True) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, include_sample)
    return analysis.dashboard(sessions)


# ---- 予測 -----------------------------------------------------------------


@app.post("/api/predict")
def predict(body: PredictIn, include_sample: bool = True) -> dict:
    with db.connect() as conn:
        sessions = db.fetch_sessions(conn, include_sample)

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


# ---- サンプルデータ -------------------------------------------------------


@app.post("/api/sample", status_code=201)
def add_sample(n: int = Query(default=60, ge=1, le=300)) -> dict:
    """動作確認用のサンプル記録を追加する(is_sample=1 で区別され、後で一括削除できる)。"""
    items = seed.generate_sessions(n)
    with db.connect() as conn:
        for item in items:
            db.insert_session(conn, {**item, "visited_on": item["visited_on"].isoformat()}, is_sample=True)
    return {"added": len(items)}


@app.delete("/api/sample")
def clear_sample() -> dict:
    with db.connect() as conn:
        return {"deleted": db.delete_samples(conn)}


# ---- フロントエンド -------------------------------------------------------
# API ルートより後に登録すること(先に登録すると / が API を食ってしまう)。
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
