from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class DailyDigestItem(BaseModel):
    market_id: str
    title: str
    detail: str
    reason_why_it_matters: str


class DailyDigestResponse(BaseModel):
    headline: str
    summary: str
    items: list[DailyDigestItem] = []


class ActionResponse(BaseModel):
    action: str | None = None
    status: str
    summary: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    technical_log: str = ""
    returncode: int | None = None


class QualityMetricGlossaryItem(BaseModel):
    id: str
    label: str
    what: str
    why: str
    how: str


class QualityMetricSummary(BaseModel):
    brier_research: float | None = None
    brier_naked: float | None = None
    brier_market: float | None = None
    edge_research_vs_market: float | None = None
    edge_naked_vs_market: float | None = None


class QualityCategoryRow(BaseModel):
    category: str
    resolved: int
    brier_research: float | None = None
    brier_market: float | None = None


class QualityResponse(BaseModel):
    resolved_predictions: int
    minimum_needed: int
    status: Literal["INSUFFICIENT_DATA", "COMPLETE"]
    decision: Literal["COLLECT", "GO", "PIVOT", "STOP"]
    summary: str
    metrics: QualityMetricSummary
    category_breakdown: list[QualityCategoryRow] = []
    calibration_image: str | None = None
    glossary: list[QualityMetricGlossaryItem] = []


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
    open_shadow_positions: int = 0
    open_operator_positions: int = 0
    due_reviews_count: int = 0
    latest_run: LatestRunSummary
    top_signals: list[dict[str, Any]]
    attention_items: list[dict[str, Any]] = []
    daily_digest: DailyDigestResponse | None = None
    next_step: str = ""
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


class MarketTransparency(BaseModel):
    facts: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    falsifier: str | None = None
    updated_at: str | None = None


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
    last_updated: str | None = None
    history: list[MarketHistoryPoint] = Field(default_factory=list)
    transparency: MarketTransparency = Field(default_factory=MarketTransparency)
    research: dict[str, Any]


class WatchlistItemRequest(BaseModel):
    market_id: str
    note: str = ""


class WatchlistResponse(BaseModel):
    items: list[dict[str, Any]]


class MarketWorkspaceResponse(BaseModel):
    market: dict[str, Any]
    signal: dict[str, Any] | None = None
    open_positions: list[dict[str, Any]] = Field(default_factory=list)
    due_reviews: list[dict[str, Any]] = Field(default_factory=list)
    recent_decisions: list[dict[str, Any]] = Field(default_factory=list)
    latest_postmortem: dict[str, Any] | None = None


class OpenPositionsResponse(BaseModel):
    items: list[dict[str, Any]]


class DueReviewsResponse(BaseModel):
    items: list[dict[str, Any]]


class ManualDecisionRequest(BaseModel):
    signal_id: int
    decision_type: str
    decision_reason: str
    operator_confidence: str | None = None
    confirmation_criteria: str | None = None
    invalidation_criteria: str | None = None
    open_position: bool = False
    review_due_at: str | None = None


class ManualDecisionResponse(BaseModel):
    ok: bool
    decision_id: int
    position_id: int | None = None
    market_id: str


class PositionCloseRequest(BaseModel):
    note: str = ""


class PositionCloseResponse(BaseModel):
    position_id: int
    status: str
    close_reason: str | None = None
    closed_yes_price: float | None = None
    pnl_usd: float | None = None
    pnl_percent: float | None = None


class ReviewCompleteRequest(BaseModel):
    note: str = ""


class ReviewCompleteResponse(BaseModel):
    ok: bool
    review_id: int
    status: str
    market_id: str
