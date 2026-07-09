"""구조적 로깅 테스트 (Phase 8) — 로그 라인에 client_id/draft_id 주입 확인."""

from __future__ import annotations

import io
import logging

from web.logging_config import (
    ContextFilter,
    bind_log_context,
    clear_log_context,
)

_FMT = "%(levelname)s [cid=%(client_id)s did=%(draft_id)s] %(message)s"


def _capture(logger_name: str) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(ContextFilter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream, handler


def test_context_injected_into_log_line():
    logger, stream, handler = _capture("consult.test.bound")
    try:
        bind_log_context(client_id=1001, draft_id=42)
        logger.info("analysis started")
        out = stream.getvalue()
        assert "cid=1001" in out and "did=42" in out
    finally:
        clear_log_context()
        logger.removeHandler(handler)


def test_context_defaults_to_dash():
    logger, stream, handler = _capture("consult.test.unbound")
    try:
        clear_log_context()
        logger.info("no context")
        out = stream.getvalue()
        assert "cid=- did=-" in out
    finally:
        logger.removeHandler(handler)
