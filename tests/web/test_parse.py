"""parse 엔드포인트 E2E (Phase 9) — assist→게이트→compute 흐름.

fake parse_fn 을 주입해 실제 LLM 없이: 원시→파싱 초안(+대사)→전문가 초안을 /ingest 로 유입→computed
도달을 검증한다. admin 전용(무토큰 401)도 확인한다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.session import create_all, make_session_factory
from orchestrator import Orchestrator
from rag.chroma_client import InMemoryCaseStore
from tests.web.conftest import (
    _fake_bs,
    _fake_final,
    _fake_pl,
    _fake_report,
    auth_header,
    login,
)
from web.app import create_app


def _fake_parse(raw_text, *, client_id, period, approved_memory=None):
    sales = [{"item_name": "후라이드치킨", "selling_price": 18000, "unit_cost": 7200, "quantity": 3900}]
    rev = 18000 * 3900
    return {
        "info": {"client_id": client_id, "period": period, "business_days": 78, "total_revenue": rev},
        "sales": sales,
        "cogs": [{"material_category": "생닭", "amount": 41000000, "prev_amount": 33000000}],
        "opex": [{"account_name": "임차료", "amount": 6000000, "prev_amount": 6000000}],
        "wc": {"ar_days": 33.0, "ap_days": 28.0},
        "inv": [{"category": "포장재", "amount": 4500000, "days_in_inventory": 41.0}],
        "debt": [{"lender": "○○은행", "amount": 60000000, "interest_rate": 5.2, "monthly_payment": 1850000}],
        "od": {"suspense_receipts": 12000000, "suspense_payments": 3500000},
        "bs": {"total_cash": 24000000, "total_ca": 95000000, "total_cl": 62000000, "total_equity": 86000000},
        "schema_extensions": [{"target_sheet": "OPEX", "generated_key": "government_subsidy",
                               "korean_name": "손실보전금", "value": 3000000}],
        "raw_total_check": {"source_raw_sum": rev},
    }


@pytest.fixture()
def parse_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    create_all(engine)
    sf = make_session_factory(engine)
    from web.auth import seed_default_users
    seed_default_users(sf)
    from web.ratelimit import limiter
    limiter.enabled = False
    limiter.reset()
    case_store = InMemoryCaseStore()

    def make_orch(store):
        return Orchestrator(store, pl_fn=_fake_pl, bs_fn=_fake_bs, report_fn=_fake_report,
                            publish_fn=_fake_final, case_store=case_store)

    app = create_app(session_factory=sf, make_orchestrator=make_orch, parse_fn=_fake_parse)
    from fastapi.testclient import TestClient
    with TestClient(app) as tc:
        token = login(tc, "admin", "admin-secret")
        tc.headers.update(auth_header(token))
        yield tc


def test_parse_returns_draft_and_reconciliation(parse_client):
    r = parse_client.post("/api/consulting/parse",
                          json={"client_id": "C-1001", "period": "2025-Q3", "raw_text": "원시..."})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["reconciliation"]["matched"] is True
    assert "pl_raw" in d and "bs_raw" in d
    assert d["schema_extensions"][0]["generated_key"] == "government_subsidy"
    assert d["bs_raw"]["accounts"]["DEBT"]["amount"] == 60000000  # 집계


def test_parse_requires_admin(parse_client):
    # 토큰 제거 → 401
    r = parse_client.post("/api/consulting/parse", headers={"Authorization": ""},
                          json={"client_id": "C", "period": "P", "raw_text": "x"})
    assert r.status_code == 401


def test_assist_flow_parse_then_ingest_reaches_computed(parse_client):
    """파싱 초안을 전문가가 확정(/ingest)하면 computed 도달 — assist→게이트→compute."""
    # 1) 고객 등록(숫자 client_id 획득)
    cid = parse_client.post("/api/clients", json={
        "name": "김사장", "industry": "치킨전문점", "district_type": "office",
        "owner_gender": "female", "owner_age_band": "30대 초중반", "risk_appetite": "conservative",
    }).json()["client_id"]
    # 2) 원시 → 파싱 초안
    draft = parse_client.post("/api/consulting/parse",
                              json={"client_id": "C-1001", "period": "2025-Q3", "raw_text": "원시..."}).json()
    # 3) 전문가 확정 업로드(초안 draft를 /ingest로) → computed
    r = parse_client.post("/api/consulting/ingest", json={
        "client_id": cid, "period": "2025-Q3",
        "pl_raw": draft["pl_raw"], "bs_raw": draft["bs_raw"],
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "computed"
