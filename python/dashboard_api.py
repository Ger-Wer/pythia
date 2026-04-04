from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

from python.dashboard_auth import DashboardAuthConfig, build_serializer, verify_password
from python.dashboard_models import (
    LatestRunResponse,
    LoginRequest,
    MarketDetailsResponse,
    MeResponse,
    OverviewResponse,
    WatchlistItemRequest,
    WatchlistResponse,
)
from python.operator_core import OperatorCore, SAFE_ACTION_NAMES

SESSION_COOKIE_NAME = "pythia_dashboard_session"


def create_app(
    db_path: Path | str,
    log_dir: Path | str,
    session_secret: str,
    dashboard_username: str,
    dashboard_password_hash: str,
    cookie_secure: bool = False,
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

    @app.get("/api/watchlist", response_model=WatchlistResponse)
    def watchlist(request: Request) -> WatchlistResponse:
        require_session(request)
        return WatchlistResponse(items=core.list_watchlist())

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

    @app.post("/api/actions/{action}")
    def run_action(action: str, request: Request) -> dict:
        require_session(request)
        if action not in SAFE_ACTION_NAMES:
            raise HTTPException(status_code=404, detail="UNKNOWN_ACTION")
        return core.run_safe_action(action)

    return app
