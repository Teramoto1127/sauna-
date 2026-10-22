from app import analysis


def make(score, sets, facility="A", water_temp=15.0, date="2026-10-01", sid=1):
    return {
        "id": sid,
        "visited_on": date,
        "facility": facility,
        "sauna_temp": 90.0,
        "water_temp": water_temp,
        "sleep_hours": 7.0,
        "fatigue": "mid",
        "ate_before": False,
        "score": score,
        "note": "",
        "is_sample": False,
        "sets": [{"set_no": i + 1, "sauna_min": s, "water_min": w, "rest_min": r} for i, (s, w, r) in enumerate(sets)],
    }


def test_golden_averages_only_high_score_sessions():
    sessions = [
        make(5, [(10, 1, 10), (10, 1, 10)]),
        make(4, [(8, 2, 12)]),
        make(2, [(20, 5, 1)]),  # 低評価は平均に入らない
    ]
    g = analysis.golden(sessions)
    assert g["n_sessions"] == 2
    assert g["n_total"] == 3
    # セッション内平均 → セッション間平均: サウナ (10+8)/2, 水風呂 (1+2)/2, 休憩 (10+12)/2
    assert g["sauna_min"] == 9.0
    assert g["water_min"] == 1.5
    assert g["rest_min"] == 11.0
    assert g["ratio"] == {"sauna": 6.0, "water": 1.0, "rest": 7.3}


def test_golden_returns_none_without_high_scores():
    assert analysis.golden([make(3, [(9, 1, 9)])]) is None
    assert analysis.golden([]) is None


def test_golden_ratio_omitted_when_no_water_bath():
    g = analysis.golden([make(5, [(10, 0, 10)])])
    assert g["ratio"] is None


def test_report_splits_by_water_temperature_and_facility():
    sessions = [
        make(5, [(9, 1, 10)], facility="A", water_temp=12.0, sid=1),
        make(5, [(11, 2, 14)], facility="B", water_temp=18.0, sid=2),
        make(2, [(9, 1, 5)], facility="B", water_temp=18.0, sid=3),
    ]
    r = analysis.golden_report(sessions)
    assert r["cold_water"]["n_sessions"] == 1
    assert r["cold_water"]["golden"]["sauna_min"] == 9.0
    assert r["warm_water"]["n_sessions"] == 2
    assert r["warm_water"]["golden"]["sauna_min"] == 11.0
    by_name = {f["facility"]: f for f in r["by_facility"]}
    assert by_name["B"]["n_sessions"] == 2
    assert by_name["B"]["avg_score"] == 3.5


def test_report_ignores_sessions_without_water_temp_in_temperature_split():
    s = make(5, [(9, 1, 10)])
    s["water_temp"] = None
    r = analysis.golden_report([s])
    assert r["cold_water"]["n_sessions"] == 0
    assert r["warm_water"]["n_sessions"] == 0
    assert r["overall"] is not None


def test_dashboard_empty():
    d = analysis.dashboard([])
    assert d["total_sessions"] == 0
    assert d["score_trend"] == []


def test_dashboard_aggregates():
    sessions = [
        make(4, [(10, 1, 10), (8, 1, 12)], date="2026-09-30", sid=1),
        make(2, [(6, 2, 6)], date="2026-10-02", sid=2),
    ]
    d = analysis.dashboard(sessions)
    assert d["total_sessions"] == 2
    assert d["total_sets"] == 3
    assert d["avg_score"] == 3.0
    assert [m["month"] for m in d["monthly_counts"]] == ["2026-09", "2026-10"]
    first = d["set_distribution"][0]
    assert first["set_no"] == 1 and first["n"] == 2 and first["sauna"] == 8.0
    assert d["calendar"][0]["date"] == "2026-09-30"


# ---- 体調・条件別 ----------------------------------------------------------


def _s(i, sleep, fatigue, ate, score, sets=((9, 1.5, 12),)):
    return {
        "id": i, "visited_on": f"2026-09-{i:02d}", "facility": "X", "sauna_temp": 90, "water_temp": 15,
        "sleep_hours": sleep, "fatigue": fatigue, "ate_before": ate, "score": score, "note": "",
        "sets": [{"set_no": n + 1, "sauna_min": a, "water_min": b, "rest_min": c} for n, (a, b, c) in enumerate(sets)],
    }


def test_by_condition_splits_groups_and_flags_small_samples():
    from app import analysis

    sessions = [_s(i, 5, "high", False, 4, ((7, 1, 10),)) for i in range(1, 6)]          # 睡眠<6 が5件(すべて高得点)
    sessions += [_s(10 + i, 7, "mid", True, 5 if i else 2, ((11, 2, 14),)) for i in range(2)]  # 睡眠6〜8 が2件
    sessions.append(_s(20, None, "low", False, 3))                                         # 睡眠不明

    cond = analysis.by_condition(sessions)
    sleep = {g["key"]: g for g in next(d for d in cond["dimensions"] if d["key"] == "sleep")["groups"]}
    assert sleep["short"]["n_sessions"] == 5 and sleep["short"]["reliable"] is True
    assert sleep["short"]["golden"]["sauna_min"] == 7.0
    assert sleep["mid"]["n_sessions"] == 2 and sleep["mid"]["reliable"] is False
    assert sleep["long"]["n_sessions"] == 0 and sleep["long"]["golden"] is None
    assert sleep["short"]["best"]["score"] == 4

    fatigue = {g["key"]: g for g in next(d for d in cond["dimensions"] if d["key"] == "fatigue")["groups"]}
    assert fatigue["high"]["n_sessions"] == 5 and fatigue["low"]["n_sessions"] == 1

    gaps = {(g["dimension"], g["label"]): g for g in cond["gaps"]}
    assert gaps[("前日の睡眠", "8時間以上")]["needed"] == 5
    assert gaps[("前日の睡眠", "6〜8時間")]["needed"] == 3
    assert ("前日の睡眠", "6時間未満") not in gaps


def test_by_condition_handles_no_sessions():
    from app import analysis

    cond = analysis.by_condition([])
    assert all(g["n_sessions"] == 0 and g["golden"] is None for d in cond["dimensions"] for g in d["groups"])
    assert len(cond["gaps"]) == sum(len(d["groups"]) for d in cond["dimensions"])
