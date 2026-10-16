"""「ととのい黄金比」の算出とダッシュボード用の集計。

入力は db.fetch_sessions() が返す dict のリスト。DB に依存しない純粋関数にしてあり、
単体テストしやすい。
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Optional

HIGH_SCORE = 4          # 「ととのった」とみなす最低スコア
COLD_WATER_C = 14.0     # 「冷たい水風呂」とみなす温度(以下)


def session_means(session: dict) -> dict:
    """1セッションの、セットあたり平均の時間配分。"""
    sets = session["sets"]
    return {
        "sauna": mean(s["sauna_min"] for s in sets),
        "water": mean(s["water_min"] for s in sets),
        "rest": mean(s["rest_min"] for s in sets),
    }


def ratio_label(sauna: float, water: float, rest: float) -> str:
    return f"サウナ{sauna:.1f}分 : 水風呂{water:.1f}分 : 休憩{rest:.1f}分"


def golden(sessions: list[dict], min_score: int = HIGH_SCORE) -> Optional[dict]:
    """高スコアのセッションの平均時間配分(=黄金比)。該当がなければ None。"""
    high = [s for s in sessions if s["score"] >= min_score and s["sets"]]
    if not high:
        return None

    means = [session_means(s) for s in high]
    sauna = mean(m["sauna"] for m in means)
    water = mean(m["water"] for m in means)
    rest = mean(m["rest"] for m in means)

    # 水風呂を 1 とした比率(水風呂が 0 に近いときは出さない)
    ratio = None
    if water >= 0.1:
        ratio = {
            "sauna": round(sauna / water, 1),
            "water": 1.0,
            "rest": round(rest / water, 1),
        }

    return {
        "n_sessions": len(high),
        "n_total": len(sessions),
        "sauna_min": round(sauna, 1),
        "water_min": round(water, 1),
        "rest_min": round(rest, 1),
        "avg_score": round(mean(s["score"] for s in high), 2),
        "ratio": ratio,
        "label": ratio_label(sauna, water, rest),
    }


def golden_report(sessions: list[dict]) -> dict:
    """全体・水風呂温度別・施設別の黄金比をまとめて返す。"""
    with_temp = [s for s in sessions if s["water_temp"] is not None]
    cold = [s for s in with_temp if s["water_temp"] <= COLD_WATER_C]
    warm = [s for s in with_temp if s["water_temp"] > COLD_WATER_C]

    by_facility: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        if s["facility"]:
            by_facility[s["facility"]].append(s)

    facilities = [
        {
            "facility": name,
            "n_sessions": len(items),
            "avg_score": round(mean(i["score"] for i in items), 2),
            "golden": golden(items),
        }
        for name, items in by_facility.items()
    ]
    facilities.sort(key=lambda f: (-f["n_sessions"], f["facility"]))

    return {
        "n_sessions": len(sessions),
        "high_score_threshold": HIGH_SCORE,
        "overall": golden(sessions),
        "cold_water": {
            "threshold_c": COLD_WATER_C,
            "n_sessions": len(cold),
            "golden": golden(cold),
        },
        "warm_water": {
            "threshold_c": COLD_WATER_C,
            "n_sessions": len(warm),
            "golden": golden(warm),
        },
        "by_facility": facilities,
    }


def dashboard(sessions: list[dict]) -> dict:
    """グラフ・カレンダー用の集計。"""
    if not sessions:
        return {
            "total_sessions": 0,
            "total_sets": 0,
            "avg_score": None,
            "score_trend": [],
            "monthly_counts": [],
            "set_distribution": [],
            "facilities": [],
            "calendar": [],
        }

    monthly: dict[str, int] = defaultdict(int)
    for s in sessions:
        monthly[s["visited_on"][:7]] += 1

    # セット番号ごとの平均時間(1セット目はこう、2セット目はこう、という分布)
    by_set_no: dict[int, list[dict]] = defaultdict(list)
    for s in sessions:
        for st in s["sets"]:
            by_set_no[st["set_no"]].append(st)
    set_distribution = [
        {
            "set_no": no,
            "n": len(items),
            "sauna": round(mean(i["sauna_min"] for i in items), 1),
            "water": round(mean(i["water_min"] for i in items), 1),
            "rest": round(mean(i["rest_min"] for i in items), 1),
        }
        for no, items in sorted(by_set_no.items())
    ]

    facility_stats: dict[str, list[int]] = defaultdict(list)
    for s in sessions:
        if s["facility"]:
            facility_stats[s["facility"]].append(s["score"])
    facilities = sorted(
        (
            {"facility": f, "count": len(v), "avg_score": round(mean(v), 2)}
            for f, v in facility_stats.items()
        ),
        key=lambda x: (-x["count"], x["facility"]),
    )

    return {
        "total_sessions": len(sessions),
        "total_sets": sum(len(s["sets"]) for s in sessions),
        "avg_score": round(mean(s["score"] for s in sessions), 2),
        "score_trend": [
            {"id": s["id"], "date": s["visited_on"], "score": s["score"], "facility": s["facility"]}
            for s in sessions
        ],
        "monthly_counts": [{"month": m, "count": c} for m, c in sorted(monthly.items())],
        "set_distribution": set_distribution,
        "facilities": facilities,
        "calendar": [
            {
                "id": s["id"],
                "date": s["visited_on"],
                "facility": s["facility"],
                "score": s["score"],
                "n_sets": len(s["sets"]),
            }
            for s in sessions
        ],
    }
