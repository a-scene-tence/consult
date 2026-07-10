"""공개 사장님 리포트 조회 API 테스트 — GET /api/consulting/reports/shared/{token}/latest.

IDOR 방어: URL에 정수 id 없이 추측 불가 report_token 으로만 조회한다. 무인증(퍼블릭)으로
최신 발행본 dashboard_payload 를 반환하는지, 미발행/미존재 토큰 시 404 인지 검증한다.
발행 워크플로는 admin 이 수행하고, 조회는 인증 헤더 없는 별도 TestClient 로 확인한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from db.models import Client
from tests.web.test_e2e import _ingest, _register


def _token_for(client, client_id: int) -> str:
    """발행 고객의 report_token(공유 링크 토큰)을 DB에서 조회한다."""
    with client.app_session_factory() as session:
        token = session.execute(
            select(Client.report_token).where(Client.id == client_id)
        ).scalar_one()
    assert token and len(token) == 32
    return token


def _publish(client) -> int:
    """등록→ingest→start→feedback→approve 로 published 도달, client_id 반환."""
    client_id = _register(client)
    draft_id = _ingest(client, client_id)
    assert client.post(f"/api/consulting/{draft_id}/start").status_code == 202
    assert client.post(
        f"/api/consulting/{draft_id}/feedback",
        json={"reviewer": "세무 파트너", "overall_note": "톤 부드럽게", "instructions": []},
    ).status_code == 202
    assert client.post(f"/api/consulting/{draft_id}/approve").status_code == 200
    return client_id


def test_public_latest_report_no_auth(client):
    client_id = _publish(client)
    token = _token_for(client, client_id)

    # 인증 헤더 없는 퍼블릭 클라이언트로 토큰 조회 → 200 + dashboard_payload
    public = TestClient(client.app)
    r = public.get(f"/api/consulting/reports/shared/{token}/latest")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["client"]["name"] == "테스트 가게"
    hero_keys = {k["key"] for k in payload["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero_keys


def test_public_latest_report_404_before_publish(client):
    client_id = _register(client)  # 발행 전
    token = _token_for(client, client_id)
    public = TestClient(client.app)
    assert public.get(f"/api/consulting/reports/shared/{token}/latest").status_code == 404


def test_public_latest_report_404_unknown_token(client):
    public = TestClient(client.app)
    assert public.get("/api/consulting/reports/shared/deadbeefdeadbeef/latest").status_code == 404
