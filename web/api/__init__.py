"""REST API 라우터 (SPEC §1). 앱 state 에서 store/orchestrator 를 조립하는 헬퍼 제공."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from orchestrator import ALLOWED_TRANSITIONS, IllegalTransition, SqlAlchemyStore


def get_session_factory(request: Request) -> Any:
    return request.app.state.session_factory


def get_store(request: Request) -> SqlAlchemyStore:
    return SqlAlchemyStore(request.app.state.session_factory)


def get_orchestrator(request: Request) -> Any:
    return request.app.state.make_orchestrator(get_store(request))


def require_draft(store: SqlAlchemyStore, draft_id: int) -> dict[str, Any]:
    """draft 존재를 보장하고 레코드를 반환(없으면 404)."""
    from web.services import draft_exists

    if not draft_exists(store.session_factory, draft_id):
        raise HTTPException(status_code=404, detail=f"draft {draft_id} 없음")
    return store.get_draft(draft_id)


def ensure_transition(current: str, target: str) -> None:
    """target 이 현재 상태에서 허용되지 않으면 IllegalTransition(→409)."""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(f"'{current}' → '{target}' 전이는 허용되지 않습니다.")
