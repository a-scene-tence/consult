"""사장님(Client)용 공개 리포트 조회 API — 모바일 리포트(client_report.html) 바인딩용.

카카오톡 링크 등으로 유입되는 사장님은 JWT 를 갖지 않으므로, 인증 없는 **공개 조회**
엔드포인트를 제공한다. 반환은 최신 발행본의 `dashboard_payload`(hero_kpis·report_sections 등)
이며, PII 없는 고객 대면 콘텐츠다(schemas/dashboard_payload.json). admin 전용
`/api/dashboard/{client_id}` 와 달리 토큰 검사를 하지 않는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from web.api import get_session_factory
from web.services import get_dashboard

router = APIRouter(prefix="/api/consulting/reports", tags=["reports"])


@router.get("/{client_id}/latest")
def latest_report(client_id: int, request: Request) -> dict[str, Any]:
    """해당 고객의 최신 발행 리포트(dashboard_payload)를 공개 반환. 미발행 시 404."""
    payload = get_dashboard(get_session_factory(request), client_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"client {client_id} 발행본 없음")
    return payload
