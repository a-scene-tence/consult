"""공개 사장님 리포트 조회 API 테스트 — GET /api/consulting/reports/{client_id}/latest.

무인증(퍼블릭)으로 최신 발행본 dashboard_payload 를 반환하는지, 미발행/미존재 시 404 인지 검증.
발행까지의 워크플로는 admin 이 수행하고, 조회는 인증 헤더 없는 별도 TestClient 로 확인한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.web.test_e2e import _ingest, _register


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

    # 인증 헤더 없는 퍼블릭 클라이언트로 조회 → 200 + dashboard_payload
    public = TestClient(client.app)
    r = public.get(f"/api/consulting/reports/{client_id}/latest")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["client"]["name"] == "테스트 가게"
    hero_keys = {k["key"] for k in payload["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero_keys


def test_public_latest_report_404_before_publish(client):
    client_id = _register(client)  # 발행 전
    public = TestClient(client.app)
    assert public.get(f"/api/consulting/reports/{client_id}/latest").status_code == 404


def test_public_latest_report_404_unknown_client(client):
    public = TestClient(client.app)
    assert public.get("/api/consulting/reports/99999/latest").status_code == 404
