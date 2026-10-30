"""「ととのい黄金比」の算出とダッシュボード用の集計。

入力は db.fetch_sessions() が返す dict のリスト。DB に依存しない純粋関数にしてあり、
単体テストしやすい。
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Optional

from datetime import date
from statistics import mean, median

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


# ---- 体調・条件別 ---------------------------------------------------------

SLEEP_SHORT_H = 6.0     # これ未満を「睡眠が短い」
SLEEP_LONG_H = 8.0      # これ以上を「睡眠が長い」
MIN_GROUP = 5           # 条件ごとに最低これだけの記録がないと「参考程度」
MIN_HIGH = 3            # 黄金比の元になる、ととのい度4以上の記録の最低数

# (次元キー, 次元名, [(グループキー, 表示名, 判定関数), ...])
CONDITION_DIMENSIONS = [
    ("sleep", "前日の睡眠", [
        ("short", f"{SLEEP_SHORT_H:g}時間未満", lambda s: s["sleep_hours"] is not None and s["sleep_hours"] < SLEEP_SHORT_H),
        ("mid", f"{SLEEP_SHORT_H:g}〜{SLEEP_LONG_H:g}時間", lambda s: s["sleep_hours"] is not None and SLEEP_SHORT_H <= s["sleep_hours"] < SLEEP_LONG_H),
        ("long", f"{SLEEP_LONG_H:g}時間以上", lambda s: s["sleep_hours"] is not None and s["sleep_hours"] >= SLEEP_LONG_H),
    ]),
    ("fatigue", "疲れ具合", [
        ("low", "元気いっぱい", lambda s: s["fatigue"] == "low"),
        ("mid", "ふつう", lambda s: s["fatigue"] == "mid"),
        ("high", "お疲れ気味", lambda s: s["fatigue"] == "high"),
    ]),
    ("ate", "サウナ前の食事", [
        ("no", "食べていない", lambda s: not s["ate_before"]),
        ("yes", "食べた", lambda s: bool(s["ate_before"])),
    ]),
]


def _best_session(items: list[dict]) -> Optional[dict]:
    """その条件でいちばん点が高かった記録(同点は新しいほう)。"""
    if not items:
        return None
    best = max(items, key=lambda s: (s["score"], s["visited_on"], s["id"]))
    m = session_means(best)
    return {
        "id": best["id"],
        "visited_on": best["visited_on"],
        "facility": best["facility"],
        "score": best["score"],
        "sauna_min": round(m["sauna"], 1),
        "water_min": round(m["water"], 1),
        "rest_min": round(m["rest"], 1),
    }


def by_condition(sessions: list[dict]) -> dict:
    """体調・条件ごとの黄金比と、記録が足りない条件の案内。

    記録が少ない条件の平均はぶれる。reliable=False は「参考程度」の印で、
    少数の記録から体調の影響を断定しないための目印。
    """
    usable = [s for s in sessions if s["sets"]]
    dimensions = []
    gaps = []
    for key, label, groups in CONDITION_DIMENSIONS:
        rows = []
        for gkey, glabel, pred in groups:
            items = [s for s in usable if pred(s)]
            g = golden(items)
            n_high = g["n_sessions"] if g else 0
            reliable = len(items) >= MIN_GROUP and n_high >= MIN_HIGH
            rows.append({
                "key": gkey,
                "label": glabel,
                "n_sessions": len(items),
                "avg_score": round(mean(i["score"] for i in items), 2) if items else None,
                "golden": g,
                "reliable": reliable,
                "best": _best_session(items),
            })
            if len(items) < MIN_GROUP:
                gaps.append({
                    "dimension": label,
                    "label": glabel,
                    "n_sessions": len(items),
                    "needed": MIN_GROUP - len(items),
                })
            elif n_high < MIN_HIGH:
                gaps.append({
                    "dimension": label,
                    "label": glabel,
                    "n_sessions": len(items),
                    "needed": 0,
                    "note": f"ととのい度{HIGH_SCORE}以上の記録があと{MIN_HIGH - n_high}件で、この条件の黄金比が出せます",
                })
        dimensions.append({"key": key, "label": label, "groups": rows})
    return {"min_group": MIN_GROUP, "min_high": MIN_HIGH, "dimensions": dimensions, "gaps": gaps}


MIN_REVIEW = 3  # 答え合わせの平均誤差を出すのに必要な、実際の記録と対応した予測の数


def review_predictions(predictions: list[dict], sessions: list[dict]) -> dict:
    """予測と、同じ日の実際の記録を並べる(答え合わせ)。

    同じ日に記録が複数あれば、いちばん後に保存した記録を実際の結果とみなす。
    誤差 = 実際のととのい度 - 予測。平均誤差は、対応した予測が MIN_REVIEW 件以上あるときだけ出す。
    """
    by_date: dict[str, dict] = {}
    for s in sorted(sessions, key=lambda x: (x["visited_on"], x["id"])):
        if s["sets"]:
            by_date[s["visited_on"]] = s

    items = []
    for p in predictions:
        actual = by_date.get(p["for_date"])
        item = {
            "for_date": p["for_date"],
            "predicted_score": p["predicted_score"],
            "plan": {"sauna_min": p["sauna_min"], "water_min": p["water_min"], "rest_min": p["rest_min"]},
            "actual": None,
        }
        if actual:
            m = session_means(actual)
            item["actual"] = {
                "score": actual["score"],
                "sauna_min": round(m["sauna"], 1),
                "water_min": round(m["water"], 1),
                "rest_min": round(m["rest"], 1),
                "facility": actual["facility"],
            }
            item["error"] = round(actual["score"] - p["predicted_score"], 2)
        items.append(item)

    matched = [i for i in items if i["actual"]]
    summary = {"n_predictions": len(items), "n_matched": len(matched), "mean_abs_error": None, "mean_error": None}
    if len(matched) >= MIN_REVIEW:
        summary["mean_abs_error"] = round(mean(abs(i["error"]) for i in matched), 2)
        summary["mean_error"] = round(mean(i["error"] for i in matched), 2)
    return {"summary": summary, "min_review": MIN_REVIEW, "items": items}


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
        "by_condition": by_condition(sessions),
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

# ---- 施設カルテ ------------------------------------------------------------

MIN_FACILITY_FIT = 3   # 「今日の体調に合う」を出すのに必要な、同じ条件での訪問数
NOTES_SHOWN = 10


def _spread(values: list) -> Optional[dict]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"median": round(median(vals), 1), "min": round(min(vals), 1), "max": round(max(vals), 1)}


def _sleep_bucket(hours: Optional[float]) -> Optional[str]:
    if hours is None:
        return None
    return "short" if hours < SLEEP_SHORT_H else "long" if hours >= SLEEP_LONG_H else "mid"


def _fit(items: list[dict], sleep_hours: Optional[float], fatigue: Optional[str]) -> Optional[dict]:
    """今日と同じ体調(睡眠の区分・疲れ)だった訪問だけの平均。条件が未指定なら None。"""
    if sleep_hours is None and fatigue is None:
        return None
    want_sleep = _sleep_bucket(sleep_hours)
    match = [
        s for s in items
        if (want_sleep is None or _sleep_bucket(s["sleep_hours"]) == want_sleep)
        and (fatigue is None or s["fatigue"] == fatigue)
    ]
    return {
        "n_sessions": len(match),
        "avg_score": round(mean(s["score"] for s in match), 2) if match else None,
        "enough": len(match) >= MIN_FACILITY_FIT,
        "reliable": len(match) >= MIN_GROUP,
    }


def facility_cards(
    sessions: list[dict],
    today: Optional[date] = None,
    sleep_hours: Optional[float] = None,
    fatigue: Optional[str] = None,
) -> list[dict]:
    """施設ごとのカルテ。サンプル・施設名なしの記録は含めない。訪問が多い順。"""
    today = today or date.today()
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        if s.get("is_sample") or not s["facility"] or not s["sets"]:
            continue
        groups[s["facility"]].append(s)

    cards = []
    for name, items in groups.items():
        items = sorted(items, key=lambda s: (s["visited_on"], s["id"]))
        scores = [s["score"] for s in items]
        last = items[-1]

        trend = None  # 直近3回の平均 - それ以前の平均(6回以上あるときだけ)
        if len(items) >= 6:
            trend = round(mean(scores[-3:]) - mean(scores[:-3]), 2)

        located = [s for s in items if s.get("lat") is not None and s.get("lng") is not None]
        location = (
            {"lat": round(mean(s["lat"] for s in located), 4), "lng": round(mean(s["lng"] for s in located), 4)}
            if located else None
        )
        notes = [
            {"id": s["id"], "visited_on": s["visited_on"], "score": s["score"], "note": s["note"]}
            for s in reversed(items) if s["note"].strip()
        ][:NOTES_SHOWN]

        cards.append({
            "facility": name,
            "n_visits": len(items),
            "first_visit": items[0]["visited_on"],
            "last_visit": last["visited_on"],
            "days_since_last": max(0, (today - date.fromisoformat(last["visited_on"])).days),
            "avg_score": round(mean(scores), 2),
            "recent_scores": scores[-10:],
            "trend": trend,
            "reliable": len(items) >= MIN_GROUP,
            "sauna_temp": _spread([s["sauna_temp"] for s in items]),
            "water_temp": _spread([s["water_temp"] for s in items]),
            "typical_sets": int(round(median(len(s["sets"]) for s in items))),
            "golden": golden(items),
            "best": _best_session(items),
            "location": location,
            "notes": notes,
            "fit": _fit(items, sleep_hours, fatigue),
        })
    cards.sort(key=lambda c: (-c["n_visits"], c["facility"]))
    return cards