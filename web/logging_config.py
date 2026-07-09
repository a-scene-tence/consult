"""구조적 로깅 (Phase 8) — 모든 로그 라인에 client_id·draft_id 주입.

`contextvars` 로 요청/태스크 스코프의 식별자를 보관하고, `logging.Filter` 가 각 로그 레코드에
그 값을 채운다. FastAPI(create_app)·Celery 워커 양쪽에서 `configure_logging()` 을 호출하면
동일 포맷으로 추적 가능한 로그가 남는다. PII(상호명·정확 위치·나이)는 로그에 남기지 않는다.
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Any

# 요청/태스크 스코프 식별자(기본값 없음 → 필터가 '-' 로 표기).
client_id_var: contextvars.ContextVar[Any] = contextvars.ContextVar("client_id", default=None)
draft_id_var: contextvars.ContextVar[Any] = contextvars.ContextVar("draft_id", default=None)

_LOG_FORMAT = "%(asctime)s %(levelname)s [cid=%(client_id)s did=%(draft_id)s] %(name)s: %(message)s"
_configured = False


class ContextFilter(logging.Filter):
    """모든 로그 레코드에 client_id/draft_id 컨텍스트를 주입한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.client_id = client_id_var.get() if client_id_var.get() is not None else "-"
        record.draft_id = draft_id_var.get() if draft_id_var.get() is not None else "-"
        return True


def bind_log_context(*, client_id: Any = None, draft_id: Any = None) -> None:
    """현재 스코프의 로그 컨텍스트를 설정(태스크/요청 시작 시 호출)."""
    if client_id is not None:
        client_id_var.set(client_id)
    if draft_id is not None:
        draft_id_var.set(draft_id)


def clear_log_context() -> None:
    client_id_var.set(None)
    draft_id_var.set(None)


def configure_logging() -> None:
    """루트 로거에 컨텍스트 필터 + 공통 포맷을 설치한다(멱등)."""
    global _configured
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(level)

    context_filter = ContextFilter()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        handler.addFilter(context_filter)
        root.addHandler(handler)
    else:
        # 기존 핸들러(uvicorn/celery)에도 포맷·필터 적용.
        for handler in root.handlers:
            handler.setFormatter(logging.Formatter(_LOG_FORMAT))
            handler.addFilter(context_filter)
    _configured = True
