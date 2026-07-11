"""DEMO_MODE 회귀 — API 키/명시 주입 없이 create_app 이 canned 배선으로 전 흐름을 구동한다.

DEMO_MODE=1 이면 파싱→ingest→분석→피드백→승인→발행→공개 리포트까지 실제 LLM 없이 동작해야 한다.
DEMO_MODE 미설정이면 훅이 동작하지 않고 기존 운영 팩토리가 선택돼야 한다(무영향).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.session import create_all, make_session_factory
from web.app import create_app
from web.auth import seed_default_users


def _fresh_sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    create_all(engine)
    sf = make_session_factory(engine)
    seed_default_users(sf)
    from web.ratelimit import limiter
    limiter.enabled = False
    limiter.reset()
    return sf


def test_demo_mode_full_flow_without_api_key(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("JWT_SECRET", "demo-secret")
    from fastapi.testclient import TestClient

    app = create_app(session_factory=_fresh_sf())  # 주입 없음 → DEMO_MODE 훅이 canned 배선
    c = TestClient(app)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "admin-secret"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    cid = c.post("/api/clients", json={
        "name": "데모사장", "trade_name": "데모분식", "industry": "분식",
        "district_type": "office", "owner_gender": "male", "owner_age": 41,
        "owner_age_band": "40s", "risk_appetite": "moderate",
    }, headers=H).json()["client_id"]

    # 파싱(Agent 0 대체) — 원시 텍스트 무관하게 대사 일치 초안 반환
    pr = c.post("/api/consulting/parse", json={
        "client_id": f"C-{cid}", "period": "2025-Q3", "raw_text": "임의 원시 데이터"}, headers=H).json()
    assert pr["reconciliation"]["matched"] is True

    did = c.post("/api/consulting/ingest", json={
        "client_id": cid, "period": "2025-Q3",
        "pl_raw": pr["pl_raw"], "bs_raw": pr["bs_raw"]}, headers=H).json()["draft_id"]
    assert c.post(f"/api/consulting/{did}/start", headers=H).status_code == 202
    assert c.post(f"/api/consulting/{did}/feedback",
                  json={"reviewer": "파트너", "overall_note": "부드럽게", "instructions": []},
                  headers=H).status_code == 202
    ap = c.post(f"/api/consulting/{did}/approve", headers=H)
    assert ap.status_code == 200
    assert ap.json()["status"] == "published"
    hero = {k["key"] for k in ap.json()["dashboard_payload"]["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero

    # 공개 모바일 리포트(무인증) — report_token 으로 조회
    from web.services import list_clients
    token = next(x["report_token"] for x in list_clients(app.state.session_factory) if x["id"] == cid)
    rep = c.get(f"/api/consulting/reports/shared/{token}/latest")
    assert rep.status_code == 200
    assert len(rep.json()["report_sections"]) == 3  # 데모 리포트 3장 카드


def test_demo_mode_off_uses_prod_factory(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    from web.production import make_prod_orchestrator

    app = create_app(session_factory=_fresh_sf())  # 주입 없음 + DEMO OFF
    assert app.state.make_orchestrator is make_prod_orchestrator  # 훅 미동작(운영 경로)


def test_demo_mode_autoseeds_client(monkeypatch):
    """DEMO_MODE=1 이면 기동 시 데모 고객 1건 자동 시드 → 드롭다운 채워짐."""
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("JWT_SECRET", "demo-secret")
    from fastapi.testclient import TestClient

    app = create_app(session_factory=_fresh_sf())  # 고객 미시드 sf → 기동 시 자동 시드
    c = TestClient(app)
    tok = c.post("/api/auth/token", data={"username": "admin", "password": "admin-secret"}).json()["access_token"]
    r = c.get("/api/clients", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert len(r.json()) >= 1  # 자동 시드된 데모 고객이 목록에 존재


def test_demo_mode_off_no_autoseed(monkeypatch):
    """DEMO OFF 면 자동 시드 미동작(고객 0명)."""
    monkeypatch.delenv("DEMO_MODE", raising=False)
    from web.services import list_clients

    sf = _fresh_sf()
    create_app(session_factory=sf)
    assert list_clients(sf) == []  # 자동 시드 없음
