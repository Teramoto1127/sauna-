"""サンプルデータの生成。

実データが少ない間でもアプリとモデルを試せるように、「それらしい」ログを作る。
隠れた真の関係(サウナ 9.5 分前後・水風呂 1.3 分前後・休憩 11 分前後が最もととのう、
水風呂が冷たく睡眠が足りているほど有利、など)にノイズを足してスコアを決めている。
あくまで動作確認用で、実際のととのい方を表すものではない。
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

# 施設ごとの平均的な室温・水風呂温度(いずれも架空)
FACILITIES = [
    ("サンプル施設A", 90.0, 16.0),
    ("サンプル施設B", 100.0, 12.0),
    ("サンプル施設C", 85.0, 18.0),
    ("サンプル施設D", 95.0, 10.0),
]

FATIGUE_PENALTY = {"low": 0.0, "mid": -0.2, "high": -0.5}


def _bump(x: float, mu: float, sd: float) -> float:
    """mu で 1、離れるほど 0 に近づく山型の関数。"""
    return math.exp(-0.5 * ((x - mu) / sd) ** 2)


def true_score(
    sauna: float,
    water: float,
    rest: float,
    water_temp: float,
    sleep: float,
    fatigue: str,
    ate: bool,
) -> float:
    """ノイズなしの「本当のととのい度」(1〜5 にクリップ前)。"""
    score = 2.0
    score += 1.0 * _bump(sauna, 9.5, 2.5)
    score += 0.8 * _bump(water, 1.3, 0.7)
    score += 0.8 * _bump(rest, 11.0, 4.0)
    score += 0.3 * max(-1.0, min(1.0, (16.0 - water_temp) / 6.0))  # 冷たいほど +
    score += 0.1 * max(-2.0, min(2.0, sleep - 6.5))                # 睡眠が長いほど +
    score += FATIGUE_PENALTY[fatigue]
    score += -0.4 if ate else 0.0
    return score


def generate_sessions(
    n: int = 60,
    seed: int = 42,
    today: date | None = None,
    span_days: int = 150,
) -> list[dict]:
    """SessionIn 互換の dict を n 件作る(古い順)。"""
    rng = random.Random(seed)
    today = today or date.today()
    out: list[dict] = []

    for _ in range(n):
        name, base_sauna_temp, base_water_temp = rng.choice(FACILITIES)
        sauna_temp = round(base_sauna_temp + rng.uniform(-3, 3), 0)
        water_temp = round(base_water_temp + rng.uniform(-2, 2), 1)
        sleep = round(rng.uniform(4.0, 9.0) * 2) / 2
        fatigue = rng.choices(["low", "mid", "high"], weights=[3, 4, 3])[0]
        ate = rng.random() < 0.3

        # その日のおおよその入り方(セットごとに少しばらつく)
        base_sauna = rng.uniform(5.0, 14.0)
        base_water = rng.uniform(0.5, 3.0)
        base_rest = rng.uniform(4.0, 18.0)
        n_sets = rng.choice([2, 3, 3, 4])

        sets = []
        for _s in range(n_sets):
            sets.append(
                {
                    "sauna_min": round(max(3.0, rng.gauss(base_sauna, 1.0)), 1),
                    "water_min": round(max(0.3, rng.gauss(base_water, 0.3)), 1),
                    "rest_min": round(max(2.0, rng.gauss(base_rest, 2.0)), 1),
                }
            )

        m_sauna = sum(s["sauna_min"] for s in sets) / n_sets
        m_water = sum(s["water_min"] for s in sets) / n_sets
        m_rest = sum(s["rest_min"] for s in sets) / n_sets
        raw = true_score(m_sauna, m_water, m_rest, water_temp, sleep, fatigue, ate)
        score = int(max(1, min(5, round(raw + rng.gauss(0, 0.4)))))

        out.append(
            {
                "visited_on": today - timedelta(days=rng.randint(0, span_days)),
                "facility": name,
                "sauna_temp": sauna_temp,
                "water_temp": water_temp,
                "sleep_hours": sleep,
                "fatigue": fatigue,
                "ate_before": ate,
                "score": score,
                "note": "",
                "sets": sets,
            }
        )

    out.sort(key=lambda s: s["visited_on"])
    return out
