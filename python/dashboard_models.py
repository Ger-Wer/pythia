from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class StatusSummary(BaseModel):
    system: Literal["ON", "DEGRADED", "OFF"]
    research: Literal["ON", "DEGRADED", "OFF"]


class LatestRunSummary(BaseModel):
    forecast_timestamp: str | None
    markets_processed: int


class ActionResponse(BaseModel):
    action: str | None = None
    status: str
    summary: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    technical_log: str = ""
    returncode: int | None = None


class HealthResponse(BaseModel):
    system_status: Literal["ON", "DEGRADED", "OFF"]
    research_status: Literal["ON", "DEGRADED", "OFF"]
    research_warning: str = ""
    checks: list[dict[str, str]]


class LogsResponse(BaseModel):
    latest_file: str | None = None
    excerpt: str = ""


class OverviewResponse(BaseModel):
    statuses: StatusSummary
    valid_predictions: int
    resolved_predictions: int
    watchlist_count: int
    latest_run: LatestRunSummary
    top_signals: list[dict[str, Any]]
    last_action: ActionResponse
    research_warning: str = ""


class LatestRunResponse(BaseModel):
    forecast_timestamp: str | None
    markets_processed: int
    items: list[dict[str, Any]]


class MarketHistoryPoint(BaseModel):
    forecast_timestamp: str | None = None
    market_price: float | None = None
    research_probability: float | None = None
    naked_probability: float | None = None


class MarketDetailsResponse(BaseModel):
    market_id: str
    question: str
    market_price: float | None = None
    research_probability: float | None = None
    naked_probability: float | None = None
    confidence: str | None = None
    verdict_summary: str = ""
    is_watched: bool = False
    reasoning: str | None = None
    key_risk: str | None = None
    base_rate: str | None = None
    volume: float | None = None
    end_date: str | None = None
    forecast_timestamp: str | None = None
    history: list[MarketHistoryPoint] = []
    research: dict[str, Any]


class WatchlistItemRequest(BaseModel):
    market_id: str
    note: str = ""


class WatchlistResponse(BaseModel):
    items: list[dict[str, Any]]
