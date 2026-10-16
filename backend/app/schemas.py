"""API の入出力スキーマ。"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Fatigue = Literal["low", "mid", "high"]


class SetIn(BaseModel):
    sauna_min: float = Field(ge=0, le=60, description="サウナ時間(分)")
    water_min: float = Field(ge=0, le=30, description="水風呂時間(分)")
    rest_min: float = Field(ge=0, le=120, description="外気浴・休憩時間(分)")


class SessionIn(BaseModel):
    visited_on: date
    facility: str = Field(default="", max_length=100)
    sauna_temp: Optional[float] = Field(default=None, ge=40, le=130)
    water_temp: Optional[float] = Field(default=None, ge=0, le=40)
    sleep_hours: Optional[float] = Field(default=None, ge=0, le=24)
    fatigue: Fatigue = "mid"
    ate_before: bool = False
    score: int = Field(ge=1, le=5, description="ととのい度(1〜5)")
    note: str = Field(default="", max_length=500)
    sets: list[SetIn] = Field(min_length=1, max_length=12)


class PlanIn(BaseModel):
    sauna_min: float = Field(ge=0, le=60)
    water_min: float = Field(ge=0, le=30)
    rest_min: float = Field(ge=0, le=120)


class PredictIn(BaseModel):
    """今日のコンディション。ここから最適な時間配分を探す。"""

    sauna_temp: Optional[float] = Field(default=None, ge=40, le=130)
    water_temp: Optional[float] = Field(default=None, ge=0, le=40)
    sleep_hours: Optional[float] = Field(default=None, ge=0, le=24)
    fatigue: Fatigue = "mid"
    ate_before: bool = False
    n_sets: int = Field(default=3, ge=1, le=12)
    plan: Optional[PlanIn] = Field(default=None, description="評価したい時間配分(任意)")
