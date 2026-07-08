"""고객용 대시보드 API (DESIGN.md B-1). 본인(client) 또는 admin 만 접근."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from web.api import get_session_factory
from web.auth import authorize_dashboard, get_current_user
from web.services import get_dashboard

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/{client_id}")
def dashboard(
    client_id: int, request: Request, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """최신 발행본의 dashboard_payload(hero_kpis 포함). admin=전체, client=본인만."""
    authorize_dashboard(client_id, user)  # 권한 위반 시 403
    payload = get_dashboard(get_session_factory(request), client_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"client {client_id} 발행본 없음")
    return payload
