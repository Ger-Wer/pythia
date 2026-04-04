from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class OverviewResponse(BaseModel):
    valid_predictions: int
    latest_run: dict[str, Any]


class LatestRunResponse(BaseModel):
    forecast_timestamp: str | None
    markets_processed: int
    items: list[dict[str, Any]]


class MarketDetailsResponse(BaseModel):
    market_id: str
    question: str
    market_price: float | None = None
    research_probability: float | None = None
    naked_probability: float | None = None
    confidence: str | None = None
    reasoning: str | None = None
    key_risk: str | None = None
    base_rate: str | None = None
    volume: float | None = None
    end_date: str | None = None
    forecast_timestamp: str | None = None
    research: dict[str, Any]


class WatchlistItemRequest(BaseModel):
    market_id: str
    note: str = ""


class WatchlistResponse(BaseModel):
    items: list[dict[str, Any]]
