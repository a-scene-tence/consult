"""FastAPI 앱 팩토리 & 페이지 라우트 (SPEC §1, DESIGN.md).

`create_app` 은 의존성을 주입 가능하게 설계한다 — 운영은 실제 Agent + DATABASE_URL, 테스트는
fake 에이전트 + SQLite + InMemoryCaseStore 를 주입해 실제 Claude 호출 없이 전 워크플로를 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestrator import IllegalTransition, Orchestrator, SqlAlchemyStore
from web.api import clients, consulting, dashboard

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def _default_session_factory() -> Any:
    from db.session import make_engine, make_session_factory

    return make_session_factory(make_engine())


def create_app(
    *,
    session_factory: Any | None = None,
    make_orchestrator: Callable[[Any], Any] | None = None,
    case_store: Any | None = None,
) -> FastAPI:
    """FastAPI 앱 생성. 미주입 시 운영 기본값(실제 Agent + DATABASE_URL)을 사용한다."""
    session_factory = session_factory or _default_session_factory()
    if make_orchestrator is None:
        def make_orchestrator(store: SqlAlchemyStore) -> Orchestrator:  # noqa: E306
            return Orchestrator(store, case_store=case_store)

    app = FastAPI(title="consult — 영세사업자 재무 컨설팅")
    app.state.session_factory = session_factory
    app.state.make_orchestrator = make_orchestrator

    # 예외 → HTTP 상태 매핑.
    @app.exception_handler(IllegalTransition)
    async def _illegal(_: Request, exc: IllegalTransition) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # API 라우터.
    app.include_router(clients.router)
    app.include_router(consulting.router)
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
