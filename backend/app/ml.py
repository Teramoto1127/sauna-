"""ととのい度の予測モデルと、最適な時間配分の探索。

流れ:
  1. 記録(セッション)から特徴量を作る
  2. 回帰モデルで「ととのい度(1〜5)」を、分類モデルで「高スコア(>=4)になる確率」を学習する
  3. 今日のコンディション(睡眠・疲労・温度など)を固定し、サウナ/水風呂/休憩の時間を
     総当たりで動かして、予測スコアが最大になる配分を探す

データが少ないうちは単純平均(analysis.golden)のほうが安定するため、
学習に必要な件数(MIN_SESSIONS)に満たない場合はモデルを使わない。
また、交差検証で「平均値を答えるだけの基準」より誤差が小さいかを必ず併記する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline

from .analysis import HIGH_SCORE, session_means

MIN_SESSIONS = 20

FATIGUE_CODE = {"low": 0, "mid": 1, "high": 2}

FEATURE_NAMES = [
    "sauna_min",
    "water_min",
    "rest_min",
    "n_sets",
    "sauna_temp",
    "water_temp",
    "sleep_hours",
    "fatigue",
    "ate_before",
]

# 探索する時間配分の範囲 (最小, 最大, 刻み)
GRID_SAUNA = (4.0, 16.0, 0.5)
GRID_WATER = (0.5, 4.0, 0.5)
GRID_REST = (3.0, 20.0, 1.0)


def _nan(value: Optional[float]) -> float:
    return float("nan") if value is None else float(value)


def session_features(session: dict) -> list[float]:
    m = session_means(session)
    return [
        m["sauna"],
        m["water"],
        m["rest"],
        float(len(session["sets"])),
        _nan(session["sauna_temp"]),
        _nan(session["water_temp"]),
        _nan(session["sleep_hours"]),
        float(FATIGUE_CODE[session["fatigue"]]),
        1.0 if session["ate_before"] else 0.0,
    ]


def build_dataset(sessions: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    usable = [s for s in sessions if s["sets"]]
    X = np.array([session_features(s) for s in usable], dtype=float)
    y = np.array([s["score"] for s in usable], dtype=float)
    return X, y


def _regressor() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "model",
                GradientBoostingRegressor(
                    n_estimators=150,
                    max_depth=2,
                    learning_rate=0.05,
                    subsample=0.8,
                    random_state=0,
                ),
            ),
        ]
    )


def _classifier() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            (
                "model",
                GradientBoostingClassifier(
                    n_estimators=100,
                    max_depth=2,
                    learning_rate=0.05,
                    subsample=0.8,
                    random_state=0,
                ),
            ),
        ]
    )


@dataclass
class TrainedModel:
    regressor: Pipeline
    classifier: Optional[Pipeline]
    n_sessions: int
    cv_mae: float
    baseline_mae: float

    def metrics(self) -> dict:
        improvement = None
        if self.baseline_mae > 0:
            improvement = round(1 - self.cv_mae / self.baseline_mae, 3)
        return {
            "n_sessions": self.n_sessions,
            "cv_mae": round(self.cv_mae, 3),
            "baseline_mae": round(self.baseline_mae, 3),
            "improvement_vs_baseline": improvement,
        }


def train(sessions: list[dict]) -> Optional[TrainedModel]:
    """学習する。件数が足りなければ None。"""
    X, y = build_dataset(sessions)
    n = len(y)
    if n < MIN_SESSIONS:
        return None

    # 交差検証で「未知のセッションをどれだけ当てられるか」を測る。
    # 比較対象は、学習データの平均スコアをそのまま答える基準(DummyRegressor)。
    k = min(5, n)
    cv = KFold(n_splits=k, shuffle=True, random_state=0)
    pred = cross_val_predict(_regressor(), X, y, cv=cv)
    base = cross_val_predict(
        Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("model", DummyRegressor(strategy="mean")),
            ]
        ),
        X,
        y,
        cv=cv,
    )
    cv_mae = float(np.mean(np.abs(pred - y)))
    baseline_mae = float(np.mean(np.abs(base - y)))

    regressor = _regressor().fit(X, y)

    # 高スコアかどうかの分類は、両方のクラスが各 3 件以上あるときだけ学習する
    high = (y >= HIGH_SCORE).astype(int)
    classifier = None
    if high.sum() >= 3 and (len(high) - high.sum()) >= 3:
        classifier = _classifier().fit(X, high)

    return TrainedModel(regressor, classifier, n, cv_mae, baseline_mae)


def _frange(lo: float, hi: float, step: float) -> np.ndarray:
    return np.round(np.arange(lo, hi + step / 2, step), 3)


def _context_row(ctx: dict) -> list[float]:
    return [
        _nan(ctx.get("sauna_temp")),
        _nan(ctx.get("water_temp")),
        _nan(ctx.get("sleep_hours")),
        float(FATIGUE_CODE[ctx.get("fatigue", "mid")]),
        1.0 if ctx.get("ate_before") else 0.0,
    ]


def _matrix(sauna: np.ndarray, water: np.ndarray, rest: np.ndarray, n_sets: int, ctx: dict) -> np.ndarray:
    """時間配分の配列(同じ長さ)と今日のコンディションから特徴量行列を作る。"""
    n = len(sauna)
    tail = np.tile(_context_row(ctx), (n, 1))
    return np.column_stack([sauna, water, rest, np.full(n, float(n_sets)), tail])


def predict_plan(model: TrainedModel, ctx: dict, sauna: float, water: float, rest: float) -> dict:
    X = _matrix(np.array([sauna]), np.array([water]), np.array([rest]), ctx.get("n_sets", 3), ctx)
    return _describe(model, X)[0] | {"sauna_min": sauna, "water_min": water, "rest_min": rest}


def _describe(model: TrainedModel, X: np.ndarray) -> list[dict]:
    score = np.clip(model.regressor.predict(X), 1.0, 5.0)
    p_high = None
    if model.classifier is not None:
        p_high = model.classifier.predict_proba(X)[:, 1]
    rows = []
    for i in range(len(X)):
        rows.append(
            {
                "predicted_score": round(float(score[i]), 2),
                "p_high": None if p_high is None else round(float(p_high[i]), 3),
            }
        )
    return rows


def optimize(model: TrainedModel, ctx: dict, top_k: int = 5) -> dict:
    """今日のコンディションで、予測スコアが最大になる時間配分を総当たりで探す。"""
    n_sets = ctx.get("n_sets", 3)
    s_axis = _frange(*GRID_SAUNA)
    w_axis = _frange(*GRID_WATER)
    r_axis = _frange(*GRID_REST)
    sauna, water, rest = (a.ravel() for a in np.meshgrid(s_axis, w_axis, r_axis, indexing="ij"))

    X = _matrix(sauna, water, rest, n_sets, ctx)
    score = np.clip(model.regressor.predict(X), 1.0, 5.0)

    # 同点が多いので、スコア降順 → サウナ短い順 → 水風呂短い順で並べる(無理のない配分を優先)
    order = np.lexsort((water, sauna, -np.round(score, 3)))

    def plan_at(idx: int) -> dict:
        row = _describe(model, X[idx : idx + 1])[0]
        return {
            "sauna_min": float(sauna[idx]),
            "water_min": float(water[idx]),
            "rest_min": float(rest[idx]),
            "predicted_score": round(float(score[idx]), 2),
            "p_high": row["p_high"],
        }

    best_idx = int(order[0])
    best = plan_at(best_idx)

    # 上位候補(サウナ時間がほぼ同じものばかり並ばないよう、サウナ時間ごとに 1 件)
    top, seen = [], set()
    for idx in order:
        key = float(sauna[idx])
        if key in seen:
            continue
        seen.add(key)
        top.append(plan_at(int(idx)))
        if len(top) >= top_k:
            break

    # グラフ用: 水風呂・休憩を最適値に固定して、サウナ時間だけ動かした予測スコア
    mask = (water == best["water_min"]) & (rest == best["rest_min"])
    curve = [
        {"sauna_min": float(s), "predicted_score": round(float(p), 2)}
        for s, p in zip(sauna[mask], score[mask])
    ]

    return {"best": best, "top": top, "sauna_curve": curve}
