"""位置情報から、行った施設の候補を出す。

1. 自分の過去の記録(位置つき)から、近い施設を探す。外部への通信はない。
2. 近くのサウナ・銭湯・温泉を OpenStreetMap(Overpass API)から探す。外部への通信がある。
   座標は約100m四方に丸めてから送る。失敗や時間切れのときは、候補なしとして続ける。
   環境変数 SAUNA_DISABLE_EXTERNAL_PLACES=1 で、外部への通信を止められる。
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from statistics import mean

KNOWN_RADIUS_M = 300       # 自分の記録の施設を「近い」とみなす距離
EXTERNAL_RADIUS_M = 500    # 外部の候補を探す範囲
AUTO_PICK_M = 150          # 自動選択に使う距離(これ以内の、自分の施設だけ)
COORD_DECIMALS = 3         # 約110m。保存・外部送信の前に丸める
OVERPASS_URL = os.environ.get("SAUNA_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
CACHE_SECONDS = 3600
TIMEOUT_S = 6

_cache: dict[tuple[float, float], tuple[float, list[dict]]] = {}


def round_coord(value: float) -> float:
    return round(float(value), COORD_DECIMALS)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の距離(m)。ハーバーサイン式。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def known_nearby(sessions: list[dict], lat: float, lng: float, radius_m: float = KNOWN_RADIUS_M) -> list[dict]:
    """自分の記録のうち、位置が近い施設を近い順に返す(施設ごとに、記録した位置の平均を使う)。"""
    groups: dict[str, list[dict]] = {}
    for s in sessions:
        if s.get("is_sample") or not s.get("facility") or s.get("lat") is None or s.get("lng") is None:
            continue
        groups.setdefault(s["facility"], []).append(s)
    out = []
    for name, items in groups.items():
        d = distance_m(lat, lng, mean(i["lat"] for i in items), mean(i["lng"] for i in items))
        if d <= radius_m:
            out.append({"facility": name, "distance_m": round(d), "n_sessions": len(items)})
    out.sort(key=lambda x: (x["distance_m"], -x["n_sessions"], x["facility"]))
    return out


def _query(lat: float, lng: float, radius_m: int) -> str:
    around = f"(around:{radius_m},{lat},{lng})"
    return (
        "[out:json][timeout:8];("
        f'nwr["leisure"="sauna"]{around};'
        f'nwr["amenity"="public_bath"]{around};'
        f'nwr["bath:type"="onsen"]{around};'
        ");out center tags 30;"
    )


def parse_overpass(data: dict, lat: float, lng: float) -> list[dict]:
    seen: set[str] = set()
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name:ja") or tags.get("name") or "").strip()
        point = el if "lat" in el else el.get("center")
        if not name or not point or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "distance_m": round(distance_m(lat, lng, point["lat"], point["lon"]))})
    out.sort(key=lambda x: (x["distance_m"], x["name"]))
    return out[:8]


def external_nearby(lat: float, lng: float) -> dict:
    """{"places": [...], "available": bool}。通信できなかったときは available=False。"""
    if os.environ.get("SAUNA_DISABLE_EXTERNAL_PLACES", "").lower() in {"1", "true", "yes"}:
        return {"places": [], "available": False}
    key = (round_coord(lat), round_coord(lng))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return {"places": hit[1], "available": True}
    try:
        body = urllib.parse.urlencode({"data": _query(key[0], key[1], EXTERNAL_RADIUS_M)}).encode("utf-8")
        req = urllib.request.Request(
            OVERPASS_URL, data=body, headers={"User-Agent": "totonoi-golden-ratio/0.1 (student project)"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:  # noqa: S310 (固定のhttps URL)
            data = json.loads(res.read().decode("utf-8"))
        places = parse_overpass(data, key[0], key[1])
    except Exception:  # 通信失敗・時間切れ・形式不正のどれでも、候補なしで続ける
        return {"places": [], "available": False}
    _cache[key] = (time.time(), places)
    return {"places": places, "available": True}
