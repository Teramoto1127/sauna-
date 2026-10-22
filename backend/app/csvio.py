"""記録のCSV書き出し・取り込み。

形式: 1セット1行。同じ record_no の行は同じ記録のセットとして束ねる。
文字コードは UTF-8(BOM付きで書き出し、Excel でも文字化けしない)。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import ValidationError

from . import checks
from .schemas import SessionIn

COLUMNS = [
    "record_no", "visited_on", "facility", "sauna_temp", "water_temp", "sleep_hours", "resting_hr",
    "fatigue", "ate_before", "score", "note", "set_no", "sauna_min", "water_min", "rest_min",
]
OPTIONAL_COLUMNS = {"resting_hr"}  # 古い書き出しファイルには無い列
MAX_BYTES = 1_000_000
MAX_ROWS = 5000
MAX_RECORDS = 1000

# 表計算ソフトで式として実行されうる先頭文字
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def _unsafe_cell(value: str) -> str:
    if len(value) > 1 and value[0] == "'" and value[1] in _FORMULA_START:
        return value[1:]
    return value


def export_csv(sessions: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(COLUMNS)
    for n, s in enumerate(sessions, start=1):
        for st in s["sets"]:
            w.writerow([
                n, s["visited_on"], _safe_cell(s["facility"]),
                "" if s["sauna_temp"] is None else s["sauna_temp"],
                "" if s["water_temp"] is None else s["water_temp"],
                "" if s["sleep_hours"] is None else s["sleep_hours"],
                "" if s.get("resting_hr") is None else s["resting_hr"],
                s["fatigue"], "yes" if s["ate_before"] else "no", s["score"], _safe_cell(s["note"]),
                st["set_no"], st["sauna_min"], st["water_min"], st["rest_min"],
            ])
    return "﻿" + out.getvalue()


def _num(row: dict, key: str) -> float | None:
    v = (row.get(key) or "").strip()
    return None if v == "" else float(v)


def _fingerprint(data: dict) -> tuple:
    return (
        str(data["visited_on"]), data.get("facility", ""), data["score"],
        tuple((round(x["sauna_min"], 2), round(x["water_min"], 2), round(x["rest_min"], 2)) for x in data["sets"]),
    )


def parse_import(raw: bytes, existing: list[dict]) -> dict:
    """CSV を検証して、取り込める記録・警告・取り込めない行を分けて返す。DB は触らない。"""
    if len(raw) > MAX_BYTES:
        return {"fatal": "ファイルが大きすぎます(1MBまで)。"}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"fatal": "文字コードがUTF-8ではありません。書き出したCSVを、UTF-8で保存し直してください。"}

    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in COLUMNS if c not in OPTIONAL_COLUMNS and c not in (reader.fieldnames or [])]
    if missing:
        return {"fatal": "列が足りません: " + ", ".join(missing)}

    groups: dict[str, list[tuple[int, dict]]] = {}
    for line_no, row in enumerate(reader, start=2):
        if line_no - 1 > MAX_ROWS:
            return {"fatal": f"行数が多すぎます({MAX_ROWS}行まで)。"}
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        groups.setdefault((row.get("record_no") or "").strip() or f"行{line_no}", []).append((line_no, row))
    if len(groups) > MAX_RECORDS:
        return {"fatal": f"記録が多すぎます({MAX_RECORDS}件まで)。"}

    known = {_fingerprint(s) for s in existing}
    valid: list[dict] = []
    warnings: list[dict] = []
    errors: list[dict] = []
    duplicates = 0

    for key, rows in groups.items():
        first_line, first = rows[0]
        try:
            data = {
                "visited_on": (first.get("visited_on") or "").strip(),
                "facility": _unsafe_cell((first.get("facility") or "").strip()),
                "sauna_temp": _num(first, "sauna_temp"),
                "water_temp": _num(first, "water_temp"),
                "sleep_hours": _num(first, "sleep_hours"),
                "resting_hr": _num(first, "resting_hr"),
                "fatigue": (first.get("fatigue") or "mid").strip() or "mid",
                "ate_before": (first.get("ate_before") or "no").strip().lower() in ("yes", "true", "1", "あり"),
                "score": int(float((first.get("score") or "").strip())),
                "note": _unsafe_cell((first.get("note") or "").strip()),
                "sets": [
                    {"sauna_min": float(r["sauna_min"]), "water_min": float(r["water_min"]), "rest_min": float(r["rest_min"])}
                    for _, r in sorted(rows, key=lambda x: float((x[1].get("set_no") or "0") or 0))
                ],
            }
            model = SessionIn.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"record": key, "line": first_line, "reason": _first_error(exc) if isinstance(exc, ValidationError) else "数値や日付の形式が正しくありません。"})
            continue

        clean = model.model_dump(mode="json")
        if _fingerprint(clean) in known:
            duplicates += 1
            continue
        known.add(_fingerprint(clean))
        valid.append(clean)
        found = checks.warnings_for(clean)
        if found:
            warnings.append({"record": key, "line": first_line, "warnings": found})

    return {"valid": valid, "warnings": warnings, "errors": errors, "duplicates": duplicates}


def _first_error(exc: ValidationError) -> str:
    e = exc.errors()[0]
    loc = ".".join(str(x) for x in e["loc"] if x != "sets") or "入力"
    return f"{loc}: {e['msg']}"
