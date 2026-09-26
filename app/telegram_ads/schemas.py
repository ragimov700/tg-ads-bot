"""Pydantic models for the subset of Telegram Ads API used by the MVP."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["EUR", "TON", "XTR"]


class AdsModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Account(AdsModel):
    account_id: str
    title: str = ""
    currency: Currency
    spent_budget: Decimal = Field(ge=0)
    remaining_budget: Decimal = Field(ge=0)
    ads_budget: Decimal = Field(default=Decimal(0), ge=0)


class Ad(AdsModel):
    ad_id: int
    title: str = ""
    currency: Currency
    cpm: Decimal = Field(ge=0)
    spent_budget: Decimal = Field(ge=0)
    remaining_budget: Decimal = Field(ge=0)
    views: int = Field(default=0, ge=0)
    opens: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    actions: int = Field(default=0, ge=0)
    action_type: str | None = None
    status: str
    is_paused: bool = False
    created_date: int = 0


class AdList(AdsModel):
    total_count: int = Field(ge=0)
    ads: list[Ad]
    next_offset: str | None = None


class AdStatItem(AdsModel):
    from_time: int
    to_time: int
    views: int = Field(default=0, ge=0)
    opens: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    actions: int = Field(default=0, ge=0)
    currency: Currency
    spent_budget: Decimal = Field(ge=0)
