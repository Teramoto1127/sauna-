import pytest

from app import places


def sess(name, lat, lng, sample=False):
    return {"facility": name, "lat": lat, "lng": lng, "is_sample": sample}


def test_distance_known_value():
    # 東京駅と新宿駅は、直線でおよそ6.1km
    d = places.distance_m(35.6812, 139.7671, 35.6896, 139.7006)
    assert 5800 < d < 6400
    assert places.distance_m(35.0, 135.0, 35.0, 135.0) == 0


def test_known_nearby_orders_by_distance_and_ignores_samples_and_far():
    sessions = [
        sess("北の湯", 35.6800, 139.7600), sess("北の湯", 35.6802, 139.7602),
        sess("南の湯", 35.6810, 139.7600),
        sess("遠い湯", 35.7500, 139.7600),
        sess("サンプル施設A", 35.6800, 139.7600, sample=True),
        {"facility": "位置なし", "lat": None, "lng": None, "is_sample": False},
    ]
    near = places.known_nearby(sessions, 35.6801, 139.7601)
    assert [n["facility"] for n in near] == ["北の湯", "南の湯"]
    assert near[0]["n_sessions"] == 2 and near[0]["distance_m"] < 30


def test_parse_overpass_handles_nodes_ways_dupes_and_no_name():
    data = {"elements": [
        {"type": "node", "lat": 35.6801, "lon": 139.7601, "tags": {"name": "駅前サウナ", "leisure": "sauna"}},
        {"type": "way", "center": {"lat": 35.6820, "lon": 139.7601}, "tags": {"name": "name-en", "name:ja": "日の出湯"}},
        {"type": "node", "lat": 35.6801, "lon": 139.7601, "tags": {"name": "駅前サウナ"}},   # 重複
        {"type": "node", "lat": 35.6801, "lon": 139.7601, "tags": {"leisure": "sauna"}},     # 名前なし
        {"type": "way", "tags": {"name": "位置なし"}},                                       # 座標なし
    ]}
    out = places.parse_overpass(data, 35.6801, 139.7601)
    assert [p["name"] for p in out] == ["駅前サウナ", "日の出湯"]
    assert out[0]["distance_m"] < 20


def test_external_nearby_failure_is_not_an_error(monkeypatch):
    places._cache.clear()
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(places.urllib.request, "urlopen", boom)
    assert places.external_nearby(35.68, 139.76) == {"places": [], "available": False}


def test_external_nearby_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SAUNA_DISABLE_EXTERNAL_PLACES", "1")
    def boom(*a, **k):
        raise AssertionError("通信してはいけない")
    monkeypatch.setattr(places.urllib.request, "urlopen", boom)
    assert places.external_nearby(35.68, 139.76)["available"] is False


def test_external_nearby_parses_and_caches(monkeypatch):
    places._cache.clear()
    calls = []

    class Res:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return '{"elements":[{"type":"node","lat":35.681,"lon":139.760,"tags":{"name":"テスト湯"}}]}'.encode("utf-8")

    def fake(req, timeout):
        calls.append(req.full_url)
        assert timeout <= 10
        assert b"around%3A500%2C35.68%2C139.76" in req.data  # 丸めた座標だけを送る
        return Res()

    monkeypatch.setattr(places.urllib.request, "urlopen", fake)
    monkeypatch.delenv("SAUNA_DISABLE_EXTERNAL_PLACES", raising=False)
    r1 = places.external_nearby(35.68020, 139.76012)
    r2 = places.external_nearby(35.68010, 139.76030)  # 同じ丸め座標 → キャッシュ
    assert r1["available"] and r1["places"][0]["name"] == "テスト湯"
    assert r2 == r1 and len(calls) == 1
