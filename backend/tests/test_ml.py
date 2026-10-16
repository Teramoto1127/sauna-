from datetime import date

import pytest

from app import analysis, ml, seed


@pytest.fixture(scope="module")
def sessions():
    items = seed.generate_sessions(80, seed=7, today=date(2026, 10, 16))
    return [
        {**s, "id": i, "visited_on": s["visited_on"].isoformat(), "is_sample": True}
        for i, s in enumerate(items, start=1)
    ]


def test_sample_data_is_plausible(sessions):
    scores = [s["score"] for s in sessions]
    assert all(1 <= x <= 5 for x in scores)
    high = sum(1 for x in scores if x >= 4) / len(scores)
    assert 0.15 <= high <= 0.6  # 全部が高評価・低評価に偏っていない


def test_train_requires_minimum_sessions(sessions):
    assert ml.train(sessions[: ml.MIN_SESSIONS - 1]) is None
    assert ml.train(sessions[: ml.MIN_SESSIONS]) is not None


def test_model_beats_mean_baseline(sessions):
    model = ml.train(sessions)
    m = model.metrics()
    assert m["cv_mae"] < m["baseline_mae"]


def test_optimize_returns_plan_inside_grid(sessions):
    model = ml.train(sessions)
    ctx = {"sauna_temp": 95, "water_temp": 14, "sleep_hours": 7, "fatigue": "mid", "ate_before": False, "n_sets": 3}
    result = ml.optimize(model, ctx)
    best = result["best"]
    assert ml.GRID_SAUNA[0] <= best["sauna_min"] <= ml.GRID_SAUNA[1]
    assert ml.GRID_WATER[0] <= best["water_min"] <= ml.GRID_WATER[1]
    assert ml.GRID_REST[0] <= best["rest_min"] <= ml.GRID_REST[1]
    assert 1 <= best["predicted_score"] <= 5
    # 候補は予測スコアの降順
    scores = [t["predicted_score"] for t in result["top"]]
    assert scores == sorted(scores, reverse=True)
    assert len(result["sauna_curve"]) > 5


def test_recommendation_is_close_to_known_optimum(sessions):
    """サンプルの真の最適(サウナ9.5分・水風呂1.3分・休憩11分付近)の近くを推薦できるか。"""
    model = ml.train(sessions)
    ctx = {"sauna_temp": 95, "water_temp": 14, "sleep_hours": 7, "fatigue": "mid", "ate_before": False, "n_sets": 3}
    best = ml.optimize(model, ctx)["best"]
    assert 7.0 <= best["sauna_min"] <= 12.0
    assert best["water_min"] <= 2.5


def test_predicted_plan_is_better_at_optimum_than_extreme(sessions):
    model = ml.train(sessions)
    ctx = {"sauna_temp": 95, "water_temp": 14, "sleep_hours": 7, "fatigue": "mid", "ate_before": False, "n_sets": 3}
    good = ml.predict_plan(model, ctx, 9.5, 1.3, 11.0)
    bad = ml.predict_plan(model, ctx, 15.5, 4.0, 3.0)
    assert good["predicted_score"] > bad["predicted_score"]


def test_handles_missing_temperature_values(sessions):
    stripped = [{**s, "sauna_temp": None, "water_temp": None} for s in sessions]
    model = ml.train(stripped)
    assert model is not None
    result = ml.optimize(model, {"fatigue": "mid", "ate_before": False, "n_sets": 3})
    assert result["best"]["predicted_score"] >= 1


def test_seed_golden_matches_true_optimum(sessions):
    g = analysis.golden(sessions)
    assert 8.0 <= g["sauna_min"] <= 11.5
    assert g["water_min"] <= 2.2
