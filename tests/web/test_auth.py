"""인증·권한 분리 E2E (Phase 6) — admin 전권 / client 본인 대시보드만 / 무토큰 401."""

from __future__ import annotations

from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from tests.web.conftest import auth_header, login

PROFILE = sample_client_profile()

_ADMIN_BODY = {
    "name": "김사장", "industry": PROFILE["industry"], "district_type": PROFILE["district_type"],
    "owner_gender": PROFILE["owner_gender"], "owner_age_band": PROFILE["owner_age_band"],
    "risk_appetite": PROFILE["risk_appetite"],
}


def _admin(tc):
    return auth_header(login(tc, "admin", "admin-secret"))


def test_login_success_and_failure(anon_client):
    assert anon_client.post(
        "/api/auth/token", data={"username": "admin", "password": "admin-secret"}
    ).status_code == 200
    assert anon_client.post(
        "/api/auth/token", data={"username": "admin", "password": "wrong"}
    ).status_code == 401


def test_no_token_rejected(anon_client):
    assert anon_client.post("/api/clients", json=_ADMIN_BODY).status_code == 401
    assert anon_client.get("/api/dashboard/1").status_code == 401


def test_admin_can_register_client_role_cannot(anon_client):
    admin_h = _admin(anon_client)
    assert anon_client.post("/api/clients", json=_ADMIN_BODY, headers=admin_h).status_code == 201

    owner_h = auth_header(login(anon_client, "owner", "owner-secret"))
    # client 역할은 관리자 API(고객 등록) 접근 불가 → 403
    assert anon_client.post("/api/clients", json=_ADMIN_BODY, headers=owner_h).status_code == 403


def _publish_for_client1(tc, admin_h):
    """client_id=1 에 대해 발행까지 진행(owner 토큰이 client_id=1 에 바인딩되어 있음)."""
    cid = tc.post("/api/clients", json=_ADMIN_BODY, headers=admin_h).json()["client_id"]
    did = tc.post("/api/consulting/ingest", headers=admin_h, json={
        "client_id": cid, "period": "2025-Q3",
        "pl_raw": sample_pl_raw(), "bs_raw": sample_bs_raw(),
    }).json()["draft_id"]
    tc.post(f"/api/consulting/{did}/start", headers=admin_h)
    tc.post(f"/api/consulting/{did}/feedback", headers=admin_h,
            json={"reviewer": "전문가", "overall_note": "n", "instructions": []})
    tc.post(f"/api/consulting/{did}/approve", headers=admin_h)
    return cid


def test_client_sees_only_own_dashboard(anon_client):
    admin_h = _admin(anon_client)
    cid = _publish_for_client1(anon_client, admin_h)
    assert cid == 1  # fresh DB → 첫 고객 id=1 (owner 계정 바인딩)

    owner_h = auth_header(login(anon_client, "owner", "owner-secret"))
    # 본인(client_id=1) 대시보드는 조회 가능
    assert anon_client.get("/api/dashboard/1", headers=owner_h).status_code == 200
    # 타 고객(client_id=2) 대시보드는 403
    assert anon_client.get("/api/dashboard/2", headers=owner_h).status_code == 403
    # admin 은 전체 조회 가능
    assert anon_client.get("/api/dashboard/1", headers=admin_h).status_code == 200
