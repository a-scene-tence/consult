"""확장자 없는 페이지 라우트 — `/cockpit`·`/report` 가 text/html 로 서빙되는지.

일부 브라우저/프록시(Codespaces + Safari)가 `.html` URL 을 다운로드로 처리하는 문제를 피하기 위한
확장자 없는 별칭 라우트. 기존 `/static/*`·`/` 는 그대로 유지된다.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from db.session import create_all, make_session_factory
from web.app import create_app


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    create_all(engine)
    from fastapi.testclient import TestClient
    return TestClient(create_app(session_factory=make_session_factory(engine)))


def test_cockpit_and_report_served_as_html():
    c = _client()
    for path in ("/cockpit", "/report"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"], (path, r.headers["content-type"])
        assert "content-disposition" not in {k.lower() for k in r.headers}
