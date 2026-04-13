from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from python.dashboard_auth import DashboardAuthConfig, build_serializer, verify_password
from python.dashboard_models import (
    ActionResponse,
    AttentionItemsResponse,
    AttentionTransitionRequest,
    AttentionTransitionResponse,
    HealthResponse,
    LatestRunResponse,
    LoginRequest,
    LogsResponse,
    DueReviewsResponse,
    ManualDecisionRequest,
    ManualDecisionResponse,
    MarketDetailsResponse,
    MarketWorkspaceResponse,
    MeResponse,
    OpenPositionsResponse,
    OperatorVsShadowResponse,
    OverviewResponse,
    PositionCloseRequest,
    PositionCloseResponse,
    QualityResponse,
    ReviewCompleteRequest,
    ReviewCompleteResponse,
    ShadowPerformanceResponse,
    WatchlistItemRequest,
    WatchDossiersResponse,
    WatchUpdatesResponse,
    WatchlistResponse,
)
from python.operator_core import OperatorCore, SAFE_ACTION_NAMES
from python import quality_metrics, comparison_metrics

SESSION_COOKIE_NAME = "pythia_dashboard_session"


def create_app(
    db_path: Path | str,
    log_dir: Path | str,
    session_secret: str,
    dashboard_username: str,
    dashboard_password_hash: str,
    cookie_secure: bool = False,
    static_dir: Path | str | None = None,
) -> FastAPI:
    app = FastAPI(title="Pythia Dashboard API")
    auth = DashboardAuthConfig(
        session_secret=session_secret,
        dashboard_username=dashboard_username,
        dashboard_password_hash=dashboard_password_hash,
    )
    serializer = build_serializer(auth.session_secret)
    core = OperatorCore(db_path=db_path, log_dir=log_dir)

    def require_session(request: Request) -> str:
        raw = request.cookies.get(SESSION_COOKIE_NAME)
        if not raw:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        try:
            payload = serializer.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="UNAUTHORIZED") from exc
        if payload.get("username") != auth.dashboard_username:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        return str(payload["username"])

    @app.post("/api/auth/login")
    def login(body: LoginRequest, response: Response) -> dict[str, bool]:
        if body.username != auth.dashboard_username or not verify_password(auth.dashboard_password_hash, body.password):
            raise HTTPException(status_code=401, detail="INVALID_CREDENTIALS")
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=serializer.dumps({"username": body.username}),
            httponly=True,
            samesite="lax",
            secure=cookie_secure,
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE_NAME)
        return {"ok": True}

    @app.get("/api/auth/me", response_model=MeResponse)
    def me(request: Request) -> MeResponse:
        try:
            username = require_session(request)
            return MeResponse(authenticated=True, username=username)
        except HTTPException:
            return MeResponse(authenticated=False, username=None)

    @app.get("/api/overview", response_model=OverviewResponse)
    def overview(request: Request) -> OverviewResponse:
        require_session(request)
        return OverviewResponse(**core.get_overview())

    @app.get("/api/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        require_session(request)
        return HealthResponse(**core.get_health())

    @app.get("/api/quality", response_model=QualityResponse)
    def quality(request: Request) -> QualityResponse:
        require_session(request)
        return QualityResponse(**core.get_quality())

    @app.get("/api/quality/shadow-performance", response_model=ShadowPerformanceResponse)
    def shadow_performance(
        request: Request,
        mode: str = "shadow",
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ShadowPerformanceResponse:
        require_session(request)
        with sqlite3.connect(core.db_path) as conn:
            data = quality_metrics.get_shadow_performance(
                conn,
                mode=mode,
                from_date=from_date,
                to_date=to_date,
            )
        return ShadowPerformanceResponse(**data)

    @app.get("/api/quality/operator-vs-shadow", response_model=OperatorVsShadowResponse)
    def operator_vs_shadow(
        request: Request,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> OperatorVsShadowResponse:
        require_session(request)
        with sqlite3.connect(core.db_path) as conn:
            data = comparison_metrics.get_operator_vs_shadow_comparison(
                conn,
                from_date=from_date,
                to_date=to_date,
            )
        return OperatorVsShadowResponse(**data)

    @app.get("/api/logs", response_model=LogsResponse)
    def logs(request: Request) -> LogsResponse:
        require_session(request)
        return LogsResponse(**core.get_logs_summary())

    @app.get("/api/runs/latest", response_model=LatestRunResponse)
    def latest_run(request: Request) -> LatestRunResponse:
        require_session(request)
        return LatestRunResponse(**core.get_latest_run())

    @app.get("/api/markets/{market_id}", response_model=MarketDetailsResponse)
    def market_details(market_id: str, request: Request) -> MarketDetailsResponse:
        require_session(request)
        try:
            return MarketDetailsResponse(**core.get_market_details(market_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="MARKET_NOT_FOUND") from exc

    @app.get("/api/markets/{market_id}/workspace", response_model=MarketWorkspaceResponse)
    def market_workspace(market_id: str, request: Request) -> MarketWorkspaceResponse:
        require_session(request)
        try:
            return MarketWorkspaceResponse(**core.get_market_workspace(market_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="MARKET_NOT_FOUND") from exc

    @app.get("/api/positions/open", response_model=OpenPositionsResponse)
    def open_positions(request: Request) -> OpenPositionsResponse:
        require_session(request)
        return OpenPositionsResponse(items=core.list_open_positions())

    @app.get("/api/reviews/due", response_model=DueReviewsResponse)
    def due_reviews(request: Request) -> DueReviewsResponse:
        require_session(request)
        return DueReviewsResponse(items=core.list_due_reviews())

    @app.post("/api/decisions", response_model=ManualDecisionResponse)
    def decisions_create(body: ManualDecisionRequest, request: Request) -> ManualDecisionResponse:
        require_session(request)
        try:
            payload = core.create_manual_decision(
                signal_id=body.signal_id,
                decision_type=body.decision_type,
                decision_reason=body.decision_reason,
                operator_confidence=body.operator_confidence,
                confirmation_criteria=body.confirmation_criteria,
                invalidation_criteria=body.invalidation_criteria,
                open_position=body.open_position,
                review_due_at=body.review_due_at,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="SIGNAL_NOT_FOUND") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ManualDecisionResponse(**payload)

    @app.post("/api/positions/{position_id}/close", response_model=PositionCloseResponse)
    def positions_close(position_id: int, body: PositionCloseRequest, request: Request) -> PositionCloseResponse:
        require_session(request)
        try:
            payload = core.close_position_manually(position_id, body.note)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="POSITION_NOT_FOUND") from exc
        return PositionCloseResponse(**payload)

    @app.post("/api/reviews/{review_id}/complete", response_model=ReviewCompleteResponse)
    def reviews_complete(review_id: int, body: ReviewCompleteRequest, request: Request) -> ReviewCompleteResponse:
        require_session(request)
        try:
            payload = core.complete_review(review_id, body.note)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="REVIEW_NOT_FOUND") from exc
        return ReviewCompleteResponse(**payload)

    @app.get("/api/watchlist", response_model=WatchlistResponse)
    def watchlist(request: Request) -> WatchlistResponse:
        require_session(request)
        return WatchlistResponse(items=core.list_watchlist())

    @app.get("/api/watchlist/dossiers", response_model=WatchDossiersResponse)
    def watchlist_dossiers(request: Request) -> WatchDossiersResponse:
        require_session(request)
        return WatchDossiersResponse(items=core.list_watch_dossiers())

    @app.get("/api/watchlist/updates", response_model=WatchUpdatesResponse)
    def watchlist_updates(request: Request) -> WatchUpdatesResponse:
        require_session(request)
        return WatchUpdatesResponse(items=core.list_watch_updates())

    @app.get("/api/attention-items", response_model=AttentionItemsResponse)
    def attention_items(request: Request, priority: str | None = None, status: str = "open") -> AttentionItemsResponse:
        require_session(request)
        return AttentionItemsResponse(items=core.list_attention_items(priority=priority, status=status))

    @app.post("/api/attention-items/{item_id}/transition", response_model=AttentionTransitionResponse)
    def attention_transition(item_id: int, body: AttentionTransitionRequest, request: Request) -> AttentionTransitionResponse:
        require_session(request)
        try:
            payload = core.transition_attention_item(item_id, body.action)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="ATTENTION_ITEM_NOT_FOUND") from exc
        return AttentionTransitionResponse(**payload)

    @app.post("/api/watchlist")
    def watchlist_add(body: WatchlistItemRequest, request: Request) -> dict[str, bool]:
        require_session(request)
        core.add_to_watchlist(body.market_id, body.note)
        return {"ok": True}

    @app.delete("/api/watchlist/{market_id}")
    def watchlist_remove(market_id: str, request: Request) -> dict[str, bool]:
        require_session(request)
        core.remove_from_watchlist(market_id)
        return {"ok": True}

    @app.post("/api/actions/{action}", response_model=ActionResponse)
    def run_action(action: str, request: Request) -> ActionResponse:
        require_session(request)
        if action not in SAFE_ACTION_NAMES:
            raise HTTPException(status_code=404, detail="UNKNOWN_ACTION")
        return ActionResponse(**core.run_safe_action(action))

    resolved_static_dir: Path | None = None
    if static_dir is not None:
        candidate = Path(static_dir)
        if candidate.exists():
            resolved_static_dir = candidate

    if resolved_static_dir is not None:
        assets_dir = resolved_static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/")
        def serve_dashboard_root() -> FileResponse:
            return FileResponse(resolved_static_dir / "index.html")

        @app.get("/{path:path}")
        def serve_dashboard_fallback(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="NOT_FOUND")
            candidate = resolved_static_dir / path
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(resolved_static_dir / "index.html")

    return app


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def create_app_from_env() -> FastAPI:
    base_dir = Path(os.getenv("PYTHIA_BASE_DIR", str(Path(__file__).resolve().parent.parent)))
    db_path = os.getenv("DB_PATH", str(base_dir / "data" / "pythia.db"))
    log_dir = os.getenv("LOG_DIR", str(base_dir / "logs"))
    session_secret = os.getenv("DASHBOARD_SESSION_SECRET", "change-me-session-secret")
    dashboard_username = os.getenv("DASHBOARD_USERNAME", "operator")
    dashboard_password_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "plain:change-me")
    static_dir = os.getenv("DASHBOARD_STATIC_DIR", str(base_dir / "dashboard_dist"))
    cookie_secure = _env_bool("DASHBOARD_COOKIE_SECURE", False)
    return create_app(
        db_path=db_path,
        log_dir=log_dir,
        session_secret=session_secret,
        dashboard_username=dashboard_username,
        dashboard_password_hash=dashboard_password_hash,
        cookie_secure=cookie_secure,
        static_dir=static_dir,
    )


app = create_app_from_env()
