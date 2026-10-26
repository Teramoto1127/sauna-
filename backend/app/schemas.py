"""API の入出力スキーマ。"""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Fatigue = Literal["low", "mid", "high"]


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    display_name: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    agree: bool = Field(description="データの扱いへの同意(必須)")

    @field_validator("agree")
    @classmethod
    def must_agree(cls, value: bool) -> bool:
        if not value:
            raise ValueError("データの扱いへの同意が必要です")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("メールアドレスの形式を確認してください")
        return value

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("表示名を入力してください")
        return value


class ConsentIn(BaseModel):
    agree: bool

    @field_validator("agree")
    @classmethod
    def must_agree(cls, value: bool) -> bool:
        if not value:
            raise ValueError("同意が必要です")
        return value


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordConfirmIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return RegisterIn.valid_email(value)


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
    resting_hr: Optional[float] = Field(default=None, ge=30, le=200, description="安静時心拍(bpm)")
    fatigue: Fatigue = "mid"
    ate_before: bool = False
    score: int = Field(ge=1, le=5, description="ととのい度(1〜5)")
    note: str = Field(default="", max_length=500)
    lat: Optional[float] = Field(default=None, ge=-90, le=90, description="行った場所の緯度(任意)")
    lng: Optional[float] = Field(default=None, ge=-180, le=180, description="行った場所の経度(任意)")
    sets: list[SetIn] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def location_pair_and_round(self) -> "SessionIn":
        if (self.lat is None) != (self.lng is None):
            raise ValueError("緯度と経度は、両方そろえて指定してください")
        if self.lat is not None:
            # 家などの細かい位置が残らないよう、約110mに丸めて保存する
            self.lat = round(self.lat, 3)
            self.lng = round(self.lng, 3)
        return self

    @field_validator("visited_on")
    @classmethod
    def not_future(cls, value: date) -> date:
        # 時差で「今日」が1日ずれても通るよう、明日までは許す
        if value > date.today() + timedelta(days=1):
            raise ValueError("未来の日付は記録できません")
        return value


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
    # 今日の状態。どれかが True なら、予測は出さずに注意だけを返す。
    drank: bool = False
    unwell: bool = False
    restricted: bool = False
    for_date: Optional[date] = Field(default=None, description="予測の対象日。省略時はサーバーの今日")


class ResearchQueryIn(BaseModel):
    """医科学コンシェルジュへ渡す質問。長文や検索演算子の濫用を避ける。"""

    question: str = Field(min_length=2, max_length=300, description="サウナ・水風呂等に関する医学論文の質問")

    @field_validator("question")
    @classmethod
    def non_blank_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("質問を2文字以上で入力してください")
        return value
