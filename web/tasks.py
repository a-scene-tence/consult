"""Celery 태스크 (Phase 6·8) — 무거운 파이프라인 단계를 큐로 분리 + 복원력.

각 태스크는 **draft_id 만으로** 실행되며, DB 에서 컨텍스트를 재구성한다(워커는 웹 프로세스와
분리되어 app.state 를 공유하지 않으므로). 오케스트레이터는 실 Agent 를 물린 프로덕션 구성이다.

Phase 8: Anthropic API 의 **전이성 오류(429 Rate Limit·50x·타임아웃·연결)** 발생 시 태스크 자체를
**지수 백오프(retry_backoff)** 로 재시도한다. 결정적 오류(NumericGuardViolation·ValueError)는
self-healing 이 이미 처리하므로 재시도 대상에서 제외한다. 로그에는 draft_id/client_id 를 바인딩한다.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from web.celery_app import celery_app
from web.logging_config import bind_log_context

logger = logging.getLogger("consult.tasks")


def _transient_errors() -> tuple[type[BaseException], ...]:
    """재시도 대상 전이성 Anthropic 오류(SDK 버전차 대비 getattr 안전 수집)."""
    names = [
        "RateLimitError",        # 429
        "InternalServerError",   # 500
        "APITimeoutError",       # 타임아웃
        "APIConnectionError",    # 연결 실패
        "OverloadedError",       # 529 (있을 때)
        "ServiceUnavailableError",
    ]
    errs = tuple(
        e for e in (getattr(anthropic, n, None) for n in names)
        if isinstance(e, type) and issubclass(e, BaseException)
    )
    return errs or (ConnectionError,)


_TRANSIENT = _transient_errors()
_MAX_RETRIES = int(os.environ.get("CELERY_MAX_RETRIES", "3"))
_BACKOFF_MAX = int(os.environ.get("CELERY_RETRY_BACKOFF_MAX", "600"))

# 모든 파이프라인 태스크가 공유하는 재시도 정책(지수 백오프 + 지터).
_RETRY_POLICY: dict[str, Any] = {
    "autoretry_for": _TRANSIENT,
    "retry_backoff": True,        # 지수 백오프(1s→2s→4s…)
    "retry_backoff_max": _BACKOFF_MAX,
    "retry_jitter": True,         # 천둥 무리(thundering herd) 방지
    "max_retries": _MAX_RETRIES,
    "acks_late": True,
}


def _prod_runtime():
    """(orchestrator, session_factory, case_store) 프로덕션 런타임 재구성."""
    from db.session import make_engine, make_session_factory
    from orchestrator import SqlAlchemyStore
    from web.production import make_prod_orchestrator, prod_case_store

    session_factory = make_session_factory(make_engine())
    store = SqlAlchemyStore(session_factory)
    return make_prod_orchestrator(store), session_factory, prod_case_store()


@celery_app.task(name="consult.run_analysis", **_RETRY_POLICY)
def run_analysis_task(draft_id: int) -> str:
    """Agent 1~3 파이프라인(computed→…→review_pending)."""
    bind_log_context(draft_id=draft_id)
    logger.info("run_analysis 시작")
    orch, _, _ = _prod_runtime()
    orch.run_analysis(draft_id)
    logger.info("run_analysis 완료")
    return "review_pending"


@celery_app.task(name="consult.submit_feedback", **_RETRY_POLICY)
def submit_feedback_task(draft_id: int, feedback: dict[str, Any]) -> str:
    """Agent 4 재작성(review_pending→revising→review_pending)."""
    bind_log_context(draft_id=draft_id)
    logger.info("submit_feedback 시작")
    orch, _, _ = _prod_runtime()
    orch.submit_feedback(draft_id, feedback)
    logger.info("submit_feedback 완료")
    return "review_pending"


@celery_app.task(name="consult.index_case", **_RETRY_POLICY)
def index_case_task(draft_id: int) -> str | None:
    """발행 자산 마스킹·적재(비동기 RAG 훅). draft/inputs 를 DB 에서 재구성."""
    from rag.case_indexer import index_published_case

    bind_log_context(draft_id=draft_id)
    orch, session_factory, case_store = _prod_runtime()
    if case_store is None:
        return None  # RAG 비활성
    store = orch.store
    draft = store.get_draft(draft_id)
    inputs = store.load_inputs(draft_id)
    profile = inputs["client_profile"]
    bind_log_context(client_id=profile.get("client_id"))
    logger.info("index_case 시작")
    return index_published_case(
        case_store, session_factory, draft_id,
        published=draft.get("payload") or {},
        expert_feedback=store.load_latest_feedback(draft_id),
        client_profile=profile,
        financials_pl=inputs["financials_pl"],
        financials_bs=inputs["financials_bs"],
    )
