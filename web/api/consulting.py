"""컨설팅 파이프라인 API — ingest·start·조회·feedback·approve (SPEC §3.1, DESIGN.md A-1/A-3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from web.api import (
    ensure_transition,
    get_orchestrator,
    get_session_factory,
    get_store,
    require_draft,
)
from web.auth import require_admin
from web.ratelimit import STRICT_RATE_LIMIT, limiter
from web.services import get_draft_view, ingest_financials, record_feedback

# 컨설팅 파이프라인은 전부 관리자(전문가) 전용.
router = APIRouter(
    prefix="/api/consulting", tags=["consulting"], dependencies=[Depends(require_admin)]
)


class IngestIn(BaseModel):
    client_id: int
    period: str
    pl_raw: dict[str, Any]
    bs_raw: dict[str, Any]


class FeedbackIn(BaseModel):
    reviewer: str = "전문가"
    overall_note: str | None = None
    instructions: list[dict[str, Any]] = Field(default_factory=list)


# --- 백그라운드 태스크(에이전트 실행) — 신선한 store/orch 로 구동 ---
def _bg_run_analysis(app: Any, draft_id: int) -> None:
    app.state.make_orchestrator(_store_from(app)).run_analysis(draft_id)


def _bg_submit_feedback(app: Any, draft_id: int, feedback: dict[str, Any]) -> None:
    app.state.make_orchestrator(_store_from(app)).submit_feedback(draft_id, feedback)


def _store_from(app: Any):
    from orchestrator import SqlAlchemyStore

    return SqlAlchemyStore(app.state.session_factory)


@router.post("/ingest", status_code=201)
def ingest(body: IngestIn, request: Request) -> dict[str, Any]:
    """Master(JSON) → compute → financials 확정 → ReportDraft(computed)."""
    draft_id = ingest_financials(
        get_session_factory(request), body.client_id, body.period, body.pl_raw, body.bs_raw
    )
    return {"draft_id": draft_id, "status": "computed"}


@router.post("/{draft_id}/start", status_code=202)
@limiter.limit(STRICT_RATE_LIMIT)  # LLM 유발 — 엄격 제한(요금 폭탄 방어)
def start(draft_id: int, request: Request, background: BackgroundTasks) -> dict[str, Any]:
    """Agent 1~3 파이프라인을 백그라운드로 구동(computed→…→review_pending)."""
    store = get_store(request)
    draft = require_draft(store, draft_id)
    ensure_transition(draft["status"], "drafting")  # 동기 가드 → 잘못된 전이면 409
    from web.production import use_celery

    if use_celery():
        from web.tasks import run_analysis_task

        run_analysis_task.delay(draft_id)  # Redis 큐로 분리(영속)
    else:
        background.add_task(_bg_run_analysis, request.app, draft_id)
    return {"draft_id": draft_id, "status": "drafting"}


@router.get("/{draft_id}")
def get_draft(draft_id: int, request: Request) -> dict[str, Any]:
    """초안 검토 뷰(A-3) — 초안·모순 플래그·확정 수치·프로필·Follow-up."""
    view = get_draft_view(get_store(request), draft_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} 없음")
    return view


@router.post("/{draft_id}/feedback", status_code=202)
@limiter.limit(STRICT_RATE_LIMIT)  # LLM(Agent 4) 유발 — 엄격 제한
def feedback(
    draft_id: int, body: FeedbackIn, request: Request, background: BackgroundTasks
) -> dict[str, Any]:
    """전문가 피드백 저장 + Agent 4 재작성(revising)을 백그라운드로 트리거."""
    store = get_store(request)
    draft = require_draft(store, draft_id)
    ensure_transition(draft["status"], "revising")
    fb = body.model_dump()
    record_feedback(get_session_factory(request), draft_id, fb)
    from web.production import use_celery

    if use_celery():
        from web.tasks import submit_feedback_task

        submit_feedback_task.delay(draft_id, fb)  # Redis 큐로 분리(영속)
    else:
        background.add_task(_bg_submit_feedback, request.app, draft_id, fb)
    return {"draft_id": draft_id, "status": "revising"}


@router.post("/{draft_id}/approve")
def approve(draft_id: int, request: Request) -> dict[str, Any]:
    """최종 승인 + 발행(approved→published) + 비차단 RAG 적재 훅."""
    store = get_store(request)
    require_draft(store, draft_id)
    orch = get_orchestrator(request)
    orch.approve(draft_id)                       # review_pending → approved (위반 시 409)
    payload = orch.publish(draft_id)             # approved → published + KB 적재 훅
    return {
        "draft_id": draft_id,
        "status": "published",
        "dashboard_payload": payload["dashboard_payload"],
    }
