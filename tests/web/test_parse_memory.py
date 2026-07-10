"""승인 매핑 메모리(Phase 10) E2E — 저장→parse 주입(전역+고객 병합)·오염 방지·admin 전용.

fake parse_fn 이 approved_memory 를 캡처해, 전역+해당 고객 승인 매핑이 병합 주입되는지 검증한다.
target_sheet 화이트리스트 위반(400)과 무토큰(401)도 확인한다.
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
from tests.web.test_parse import _fake_parse
from web.app import create_app

_CAPTURED: dict = {}


def _capturing_parse(raw_text, *, client_id, period, approved_memory=None):
    _CAPTURED["memory"] = approved_memory
    return _fake_parse(raw_text, client_id=client_id, period=period)


@pytest.fixture()
def mem_client():
    _CAPTURED.clear()
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

    app = create_app(session_factory=sf, make_orchestrator=make_orch, parse_fn=_capturing_parse)
    from fastapi.testclient import TestClient
    with TestClient(app) as tc:
        token = login(tc, "admin", "admin-secret")
        tc.headers.update(auth_header(token))
        yield tc


def test_save_memory_returns_id_and_scope(mem_client):
    r = mem_client.post("/api/consulting/parse/memory", json={
        "raw_text": "생닭", "standard_key": "생닭", "target_sheet": "cogs"})
    assert r.status_code == 201, r.text
    assert isinstance(r.json()["memory_id"], int)
    assert r.json()["scope"] == "global"  # client_id 생략 → 전역


def test_parse_injects_global_and_client_memory_merged(mem_client):
    # 전역 매핑 1건
    mem_client.post("/api/consulting/parse/memory", json={
        "raw_text": "생닭", "standard_key": "생닭", "target_sheet": "cogs"})
    # 고객(C-1001) 특수 매핑 1건
    mem_client.post("/api/consulting/parse/memory", json={
        "client_id": "C-1001", "raw_text": "○○은행 운전자금대출",
        "standard_key": "운전자금대출", "target_sheet": "debt"})
    # 다른 고객 매핑(주입돼선 안 됨)
    mem_client.post("/api/consulting/parse/memory", json={
        "client_id": "C-9999", "raw_text": "무관", "standard_key": "무관", "target_sheet": "opex"})

    r = mem_client.post("/api/consulting/parse", json={
        "client_id": "C-1001", "period": "2025-Q3", "raw_text": "원시..."})
    assert r.status_code == 200, r.text
    assert r.json()["approved_memory_count"] == 2  # 전역 + C-1001 (C-9999 제외)

    injected = _CAPTURED["memory"]
    raws = {m["raw_text"] for m in injected}
    assert raws == {"생닭", "○○은행 운전자금대출"}
    assert injected[0]["raw_text"] == "생닭"  # 전역이 먼저(참고 우선순위)


def test_save_memory_rejects_bad_target_sheet(mem_client):
    r = mem_client.post("/api/consulting/parse/memory", json={
        "raw_text": "x", "standard_key": "y", "target_sheet": "revenue"})  # 화이트리스트 밖
    assert r.status_code == 400, r.text


def test_memory_upsert_is_idempotent(mem_client):
    a = mem_client.post("/api/consulting/parse/memory", json={
        "raw_text": "생닭", "standard_key": "생닭", "target_sheet": "cogs"}).json()["memory_id"]
    # 동일 (client_id=global, raw_text) 재저장 → 같은 id 로 갱신
    b = mem_client.post("/api/consulting/parse/memory", json={
        "raw_text": "생닭", "standard_key": "원재료-생닭", "target_sheet": "cogs"}).json()["memory_id"]
    assert a == b
    listing = mem_client.get("/api/consulting/parse/memory").json()
    assert listing["count"] == 1
    assert listing["items"][0]["standard_key"] == "원재료-생닭"  # 갱신 반영


def test_memory_requires_admin(mem_client):
    r = mem_client.post("/api/consulting/parse/memory", headers={"Authorization": ""},
                        json={"raw_text": "x", "standard_key": "y", "target_sheet": "cogs"})
    assert r.status_code == 401
