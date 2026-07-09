"""web E2E 픽스처 — TestClient + fake 에이전트 주입 (실제 Claude 호출 없음).

SQLite(StaticPool) in-memory DB + fake Agent fn + 단일 InMemoryCaseStore 를 주입한 create_app 을
TestClient 로 감싼다. 백그라운드 태스크는 TestClient 가 응답 전 동기 실행하므로 결정론적이다.
"""

from __future__ import annotations

import warnings

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.session import create_all, make_session_factory
from orchestrator import Orchestrator
from rag.chroma_client import InMemoryCaseStore
from web.app import create_app

warnings.filterwarnings("ignore")  # starlette TestClient httpx deprecation 소음 제거


# --- fake 에이전트(스키마 유효 출력, 실제 Claude 미호출) ---
def _fake_pl(**kwargs):
    return {"agent": "pl_analyst", "summary": "PL mock"}


def _fake_bs(**kwargs):
    return {"agent": "bs_analyst", "summary": "BS mock"}


def _fake_report(**kwargs):
    return {
        "agent": "report_master",
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "하루 35개면 손익분기. 현금 생존기간에 유의하세요."},
        ],
        "contradiction_flags": [
            {"between": ["pl_analyst", "bs_analyst"],
             "text": "PL 투자 확대 vs BS 현금 유보 — 상충.", "resolved": True},
        ],
        "cited_values": ["bep.daily_target_qty"],
    }


def _fake_final(**kwargs):
    return {
        "agent": "final_publisher", "version": 2,
        "sections": [{"id": "overview", "title": "종합 요약", "body_md": "하루 35개면 손익분기."}],
        "applied_feedback": [
            {"from": "전문가", "directive_ref": "overall_note",
             "how_applied": "톤 부드럽게, 현금 유보 강조", "scope": "global"},
        ],
        "dashboard_payload": {
            "client": {"name": "테스트 가게", "period": "2025년 3분기"},
            "hero_kpis": [
                {"key": "bep_attainment", "label": "손익분기점 달성률", "value_pct": 268.0},
                {"key": "daily_target_qty", "label": "일일 목표 판매수량", "value": 35},
            ],
            "kpis": [{"key": "OPM", "label": "영업이익률", "value_pct": 14.2, "trend": "flat"}],
            "followup": {"prev_period": None, "items": []},
            "charts": [], "report_sections": [],
        },
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "세금 유보 계좌 분리",
             "target_metric": "cash_runway_months", "direction": "increase"},
        ],
    }


def _build_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    create_all(engine)
    session_factory = make_session_factory(engine)
    from web.auth import seed_default_users

    seed_default_users(session_factory)  # admin / owner(client_id=1) 시드
    # Rate limiter 는 테스트 간 카운터가 누적되므로 기본 비활성(전용 테스트에서만 켠다).
    from web.ratelimit import limiter

    limiter.enabled = False
    limiter.reset()
    case_store = InMemoryCaseStore()

    def make_orchestrator(store):
        return Orchestrator(
            store, pl_fn=_fake_pl, bs_fn=_fake_bs, report_fn=_fake_report,
            publish_fn=_fake_final, case_store=case_store,
        )

    app = create_app(session_factory=session_factory, make_orchestrator=make_orchestrator)
    from fastapi.testclient import TestClient

    tc = TestClient(app)
    tc.app_session_factory = session_factory
    return tc


def login(tc, username: str, password: str) -> str:
    """토큰 발급 헬퍼."""
    r = tc.post("/api/auth/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def anon_client():
    """인증 헤더가 없는 TestClient(권한 테스트용)."""
    with _build_client() as tc:
        yield tc


@pytest.fixture()
def client():
    """admin 토큰이 기본 부착된 TestClient(워크플로 테스트용)."""
    with _build_client() as tc:
        token = login(tc, "admin", "admin-secret")
        tc.headers.update(auth_header(token))
        yield tc
