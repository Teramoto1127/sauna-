"""入力ミスの検出。

schemas.py の範囲チェックは「ありえない値」を弾く(422)。
こちらは「ありえなくはないが、打ち間違いの可能性が高い値」を見つけて知らせる。
利用者が内容を確認して「このまま保存」を選べば保存できる(confirmed=true)。
外れ値が1件混ざるだけで少数データの平均はずれるため、保存前に一度止める。
"""

from __future__ import annotations

from datetime import date, timedelta

# (下限, 上限) の外側なら警告する。単位は schemas.py と同じ。
SAUNA_TEMP_RANGE = (60.0, 110.0)
WATER_TEMP_RANGE = (4.0, 25.0)
SLEEP_RANGE = (2.0, 14.0)
RESTING_HR_RANGE = (40.0, 100.0)
SAUNA_SET_MAX = 20.0
WATER_SET_MAX = 5.0
REST_SET_MAX = 60.0
OLD_DAYS = 365


def warnings_for(data: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    out: list[str] = []

    visited = data["visited_on"]
    if isinstance(visited, str):
        visited = date.fromisoformat(visited)
    if visited < today - timedelta(days=OLD_DAYS):
        out.append(f"日付が1年以上前です({visited})。")

    def outside(value, rng):
        return value is not None and not (rng[0] <= value <= rng[1])

    if outside(data.get("sauna_temp"), SAUNA_TEMP_RANGE):
        out.append(f"サウナ室の温度 {data['sauna_temp']:g}℃ は、ふつうの範囲({SAUNA_TEMP_RANGE[0]:g}〜{SAUNA_TEMP_RANGE[1]:g}℃)から外れています。")
    if outside(data.get("water_temp"), WATER_TEMP_RANGE):
        out.append(f"水風呂の温度 {data['water_temp']:g}℃ は、ふつうの範囲({WATER_TEMP_RANGE[0]:g}〜{WATER_TEMP_RANGE[1]:g}℃)から外れています。")
    if outside(data.get("sleep_hours"), SLEEP_RANGE):
        out.append(f"睡眠 {data['sleep_hours']:g}時間 は、ふつうの範囲({SLEEP_RANGE[0]:g}〜{SLEEP_RANGE[1]:g}時間)から外れています。")

    if outside(data.get("resting_hr"), RESTING_HR_RANGE):
        out.append(f"安静時心拍 {data['resting_hr']:g}bpm は、ふつうの範囲({RESTING_HR_RANGE[0]:g}〜{RESTING_HR_RANGE[1]:g}bpm)から外れています。")

    for i, s in enumerate(data["sets"], start=1):
        if s["sauna_min"] == 0:
            out.append(f"{i}セット目のサウナが0分です。")
        if s["sauna_min"] > SAUNA_SET_MAX:
            out.append(f"{i}セット目のサウナ {s['sauna_min']:g}分 は長すぎるかもしれません({SAUNA_SET_MAX:g}分超)。")
        if s["water_min"] > WATER_SET_MAX:
            out.append(f"{i}セット目の水風呂 {s['water_min']:g}分 は長すぎるかもしれません({WATER_SET_MAX:g}分超)。")
        if s["rest_min"] > REST_SET_MAX:
            out.append(f"{i}セット目の休憩 {s['rest_min']:g}分 は長すぎるかもしれません({REST_SET_MAX:g}分超)。")
    return out
