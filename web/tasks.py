"""Celery 태스크 (Phase 6) — 무거운 파이프라인 단계를 큐로 분리.

각 태스크는 **draft_id 만으로** 실행되며, DB 에서 컨텍스트를 재구성한다(워커는 웹 프로세스와
분리되어 app.state 를 공유하지 않으므로). 오케스트레이터는 실 Agent 를 물린 프로덕션 구성이다.
"""

from __future__ import annotations

from typing import Any

from web.celery_app import celery_app


def _prod_runtime():
    """(orchestrator, session_factory, case_store) 프로덕션 런타임 재구성."""
    from db.session import make_engine, make_session_factory
    from orchestrator import SqlAlchemyStore
    from web.production import make_prod_orchestrator, prod_case_store

    session_factory = make_session_factory(make_engine())
    store = SqlAlchemyStore(session_factory)
    return make_prod_orchestrator(store), session_factory, prod_case_store()


@celery_app.task(name="consult.run_analysis")
def run_analysis_task(draft_id: int) -> str:
    """Agent 1~3 파이프라인(computed→…→review_pending)."""
    orch, _, _ = _prod_runtime()
    orch.run_analysis(draft_id)
    return "review_pending"


@celery_app.task(name="consult.submit_feedback")
def submit_feedback_task(draft_id: int, feedback: dict[str, Any]) -> str:
    """Agent 4 재작성(review_pending→revising→review_pending)."""
    orch, _, _ = _prod_runtime()
    orch.submit_feedback(draft_id, feedback)
    return "review_pending"


@celery_app.task(name="consult.index_case")
def index_case_task(draft_id: int) -> str | None:
    """발행 자산 마스킹·적재(비동기 RAG 훅). draft/inputs 를 DB 에서 재구성."""
    from rag.case_indexer import index_published_case

    orch, session_factory, case_store = _prod_runtime()
    if case_store is None:
        return None  # RAG 비활성
    store = orch.store
    draft = store.get_draft(draft_id)
    inputs = store.load_inputs(draft_id)
    return index_published_case(
        case_store, session_factory, draft_id,
        published=draft.get("payload") or {},
        expert_feedback=store.load_latest_feedback(draft_id),
        client_profile=inputs["client_profile"],
        financials_pl=inputs["financials_pl"],
        financials_bs=inputs["financials_bs"],
    )
