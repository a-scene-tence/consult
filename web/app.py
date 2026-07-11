"""FastAPI 앱 팩토리 & 페이지 라우트 (SPEC §1, DESIGN.md).

`create_app` 은 의존성을 주입 가능하게 설계한다 — 운영은 실제 Agent + DATABASE_URL, 테스트는
fake 에이전트 + SQLite + InMemoryCaseStore 를 주입해 실제 Claude 호출 없이 전 워크플로를 검증한다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from orchestrator import IllegalTransition
from web.api import auth, clients, consulting, dashboard, reports
from web.logging_config import configure_logging
from web.ratelimit import limiter

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def _default_session_factory() -> Any:
    from db.session import make_engine, make_session_factory

    return make_session_factory(make_engine())


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


def create_app(
    *,
    session_factory: Any | None = None,
    make_orchestrator: Callable[[Any], Any] | None = None,
    case_store: Any | None = None,
    parse_fn: Callable[..., Any] | None = None,
) -> FastAPI:
    """FastAPI 앱 생성. 미주입 시 운영 기본값(실제 Agent + DATABASE_URL)을 사용한다."""
    session_factory = session_factory or _default_session_factory()
    if make_orchestrator is None:
        from web.production import make_prod_orchestrator

        make_orchestrator = make_prod_orchestrator  # 실 Agent + celery-aware 적재 훅
    if parse_fn is None:
        from agents.data_engineer import parse_raw

        parse_fn = parse_raw  # Agent 0(데이터 엔지니어) — 원시→표준화 초안 파싱

    configure_logging()  # 구조적 로깅(cid/did) 포맷 설치

    # 옵트인 부트스트랩: SEED_USERS 활성 시 테이블 보장 + 기본 계정 시드(로컬/단일 컨테이너에서
    # 즉시 로그인 가능). 운영(SEED_USERS 미설정/0)은 무동작 — alembic + 명시 시드를 따른다.
    if os.environ.get("SEED_USERS", "").strip().lower() in ("1", "true", "yes"):
        try:
            from db.session import create_all
            from web.auth import seed_default_users

            with session_factory() as _s:
                create_all(_s.get_bind())  # checkfirst=True — 멱등(기존 테이블 무변경)
            seed_default_users(session_factory)  # admin/owner upsert — 멱등
        except Exception:  # noqa: BLE001 — 시드 실패가 기동을 막지 않도록
            logging.getLogger(__name__).warning("기본 사용자 시드 실패", exc_info=True)

    app = FastAPI(title="consult — 영세사업자 재무 컨설팅")
    app.state.session_factory = session_factory
    app.state.make_orchestrator = make_orchestrator
    app.state.parse_fn = parse_fn

    # CORS — 프론트 도메인 허용(ALLOWED_ORIGINS). '*'이면 credentials 비활성(스펙 준수).
    origins = _allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=("*" not in origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate Limit — slowapi(전역 기본 + 라우트별 엄격 제한). 초과 시 429.
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": f"요청 한도를 초과했습니다({exc.detail}). 잠시 후 다시 시도하세요."},
        )

    # 예외 → HTTP 상태 매핑.
    @app.exception_handler(IllegalTransition)
    async def _illegal(_: Request, exc: IllegalTransition) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # API 라우터.
    app.include_router(auth.router)
    app.include_router(clients.router)
    app.include_router(consulting.router)
    app.include_router(reports.router)  # 공개 사장님 리포트 조회(무인증)
    app.include_router(dashboard.router)

    # 정적 자산.
    static_dir = _WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 페이지(HTML) 라우트 — 데이터는 클라이언트 JS 가 API fetch.
    @app.get("/")
    async def backoffice(request: Request):  # noqa: ANN202
        return _TEMPLATES.TemplateResponse(request, "backoffice.html")

    @app.get("/backoffice/draft/{draft_id}")
    async def draft_review(request: Request, draft_id: int):  # noqa: ANN202
        return _TEMPLATES.TemplateResponse(
            request, "draft_review.html", {"draft_id": draft_id}
        )

    @app.get("/dashboard/{client_id}")
    async def client_dashboard(request: Request, client_id: int):  # noqa: ANN202
        return _TEMPLATES.TemplateResponse(
            request, "dashboard.html", {"client_id": client_id}
        )

    return app
