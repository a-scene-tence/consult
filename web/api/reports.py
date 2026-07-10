"""사장님(Client)용 공개 리포트 조회 API — 모바일 리포트(client_report.html) 바인딩용.

카카오톡 링크 등으로 유입되는 사장님은 JWT 를 갖지 않으므로, 인증 없는 **공개 조회**
엔드포인트를 제공한다. 단, 정수 client_id 를 URL에 노출하면 IDOR(값 증가로 타 고객 열람)에
취약하므로 **추측 불가한 report_token(UUID)** 으로만 조회한다. 반환은 최신 발행본의
`dashboard_payload`(hero_kpis·report_sections 등, PII 없음). admin 전용 `/api/dashboard/{id}` 와 분리.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from web.api import get_session_factory
from web.services import client_id_from_token, get_dashboard

router = APIRouter(prefix="/api/consulting/reports", tags=["reports"])


@router.get("/shared/{token}/latest")
def latest_report(token: str, request: Request) -> dict[str, Any]:
    """공유 토큰으로 해당 고객의 최신 발행 리포트(dashboard_payload)를 공개 반환.

    토큰 불일치(미존재 고객) 또는 미발행 시 404. 외부 URL에 정수 id 를 노출하지 않는다.
    """
    session_factory = get_session_factory(request)
    client_id = client_id_from_token(session_factory, token)
    if client_id is None:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
    payload = get_dashboard(session_factory, client_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="아직 발행된 리포트가 없습니다.")
    return payload
