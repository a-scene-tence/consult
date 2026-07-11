"""로그인 부트스트랩 회귀 — SEED_USERS=1 이면 기동 시 테이블 생성 + 기본 계정 시드.

버그: 로컬 기동/미시드 DB 에서 users 가 없어 로그인 불가(401/500). 수정: create_app 이
SEED_USERS 활성 시 스스로 테이블·기본 계정을 부트스트랩해 즉시 로그인되게 한다.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.session import make_session_factory
from web.app import create_app


def _fresh_session_factory():
    """사전 시드/create_all 하지 않은 빈 SQLite(StaticPool) 세션 팩토리."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    return make_session_factory(engine)


def test_seed_users_bootstrap_enables_login(monkeypatch):
    """SEED_USERS=1 → create_app 이 테이블 생성+시드 → admin 로그인 200."""
    monkeypatch.setenv("SEED_USERS", "1")
    from fastapi.testclient import TestClient

    app = create_app(session_factory=_fresh_session_factory())
    client = TestClient(app)

    r = client.post("/api/auth/token", data={"username": "admin", "password": "admin-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert r.json()["role"] == "admin"

    # 잘못된 비밀번호 → 401(회귀).
    bad = client.post("/api/auth/token", data={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401


def test_no_seed_no_bootstrap(monkeypatch):
    """SEED_USERS 미설정이면 부트스트랩 미동작(운영 기본 — 시드/테이블 생성 안 함)."""
    monkeypatch.delenv("SEED_USERS", raising=False)
    from fastapi.testclient import TestClient

    app = create_app(session_factory=_fresh_session_factory())
    client = TestClient(app, raise_server_exceptions=False)
    # users 테이블 자체가 없으므로 인증 조회가 실패(부트스트랩이 돌지 않았음을 방증).
    r = client.post("/api/auth/token", data={"username": "admin", "password": "admin-secret"})
    assert r.status_code != 200
