"""Rate Limit + CORS 테스트 (Phase 8).

- Rate Limit: STRICT 초과 호출 시 429. (전역 limiter 를 켜고 reset 후 검증, 종료 시 다시 비활성.)
- CORS: Origin 헤더 요청에 access-control-allow-origin 응답 헤더가 붙는지 검증.
"""

from __future__ import annotations

import pytest

from web.ratelimit import limiter


@pytest.fixture()
def rate_limited_client(client):
    """이 테스트 동안만 limiter 활성화(+reset). client 는 admin 토큰이 부착된 TestClient."""
    limiter.reset()
    limiter.enabled = True
    yield client
    limiter.enabled = False
    limiter.reset()


def test_strict_rate_limit_returns_429(rate_limited_client):
    """LLM 엔드포인트(start)를 STRICT(5/min) 초과 호출하면 429.

    데코레이터 한도는 라우터 의존성(require_admin) 통과 후, 엔드포인트 본문 진입 전에 평가된다.
    존재하지 않는 draft 라 본문은 404 를 내지만, 한도 초과 호출은 본문 전에 429 로 차단된다.
    """
    tc = rate_limited_client
    statuses = [tc.post("/api/consulting/999/start").status_code for _ in range(7)]
    assert 429 in statuses, f"429 미발생: {statuses}"
    assert statuses[0] != 429  # 앞쪽 호출은 한도 내(본문 도달 → 404)


def test_cors_header_present(anon_client):
    """Origin 헤더가 있으면 CORS 허용 헤더가 응답에 포함된다."""
    r = anon_client.get("/", headers={"Origin": "http://example.com"})
    assert r.headers.get("access-control-allow-origin") is not None
