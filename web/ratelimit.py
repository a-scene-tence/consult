"""Rate Limiter (Phase 8) — slowapi 기반 API 남용/요금 폭탄 방어.

모듈 전역 `limiter` 를 라우트 데코레이터(`@limiter.limit(...)`)와 `create_app` 배선이 공유한다.
- 전역 기본: `DEFAULT_RATE_LIMIT`(기본 200/minute) — 모든 라우트.
- 엄격 제한: `STRICT_RATE_LIMIT`(기본 5/minute) — LLM 을 유발하는 start·feedback 에 데코레이터로 적용.

멀티 워커 공유를 위해 `REDIS_URL` 이 있으면 Redis storage 를, 없으면 in-memory 를 쓴다.
`RATE_LIMIT_ENABLED=0` 또는 테스트에서 `limiter.enabled=False` 로 비활성화할 수 있다.
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = os.environ.get("DEFAULT_RATE_LIMIT", "200/minute")
STRICT_RATE_LIMIT = os.environ.get("STRICT_RATE_LIMIT", "5/minute")

_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "1").lower() in ("1", "true", "yes")
_STORAGE = os.environ.get("REDIS_URL")  # 있으면 다중 워커 공유, 없으면 in-memory

_limiter_kwargs: dict = {
    "key_func": get_remote_address,
    "default_limits": [DEFAULT_RATE_LIMIT],
    "enabled": _ENABLED,
}
if _STORAGE:
    _limiter_kwargs["storage_uri"] = _STORAGE

limiter = Limiter(**_limiter_kwargs)
