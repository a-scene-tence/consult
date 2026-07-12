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
from web.services import (
    get_draft_view,
    ingest_financials,
    load_parsing_memory,
    record_feedback,
    save_parsing_memory,
)

# 컨설팅 파이프라인은 전부 관리자(전문가) 전용.
router = APIRouter(
    prefix="/api/consulting", tags=["consulting"], dependencies=[Depends(require_admin)]
)


class ParseIn(BaseModel):
    client_id: str
    period: str
    raw_text: str


class IngestIn(BaseModel):
    client_id: int
    period: str
    pl_raw: dict[str, Any]
    bs_raw: dict[str, Any]


class MemoryIn(BaseModel):
    """전문가 승인 매핑(학습형 메모리). client_id 생략/None 이면 전역(공통) 매핑."""

    client_id: str | None = None
    raw_text: str
    standard_key: str
    target_sheet: str
    korean_name: str | None = None
    approved_by: str = "전문가"


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


@router.post("/parse")
@limiter.limit(STRICT_RATE_LIMIT)  # LLM(데이터 엔지니어) 유발 — 엄격 제한
def parse(body: ParseIn, request: Request) -> dict[str, Any]:
    """P5 보조: 비표준 원시 데이터 → 표준화 Master **초안** + 대사 리포트(전문가 검토용).

    compute 로 유입하지 않는다 — 전문가가 초안을 검토·보완·승인한 뒤 `/ingest` 로만 유입된다(§0.5).
    """
    from compute.reconcile import build_draft_master

    # 전역+해당 고객 승인 매핑을 로드해 Agent 0(v1.1 Rule #0) 프롬프트에 주입 → 매핑 일관성.
    approved_memory = load_parsing_memory(get_session_factory(request), body.client_id)
    parser_output = request.app.state.parse_fn(
        body.raw_text, client_id=body.client_id, period=body.period,
        approved_memory=approved_memory,
    )
    draft = build_draft_master(parser_output)
    return {"parser_output": parser_output, "approved_memory_count": len(approved_memory), **draft}


@router.post("/parse/memory", status_code=201)
def add_parse_memory(body: MemoryIn, request: Request) -> dict[str, Any]:
    """전문가 승인 매핑을 저장(학습). 다음 파싱부터 Agent 0 이 강제 재사용한다(§0.5).

    target_sheet 화이트리스트 위반 시 400(오염 방지). client_id 생략 시 전역 매핑.
    """
    memory_id = save_parsing_memory(
        get_session_factory(request),
        client_id=body.client_id,
        raw_text=body.raw_text,
        standard_key=body.standard_key,
        target_sheet=body.target_sheet,
        korean_name=body.korean_name,
        approved_by=body.approved_by,
    )
    return {"memory_id": memory_id, "scope": "global" if body.client_id is None else body.client_id}


@router.get("/parse/memory")
def list_parse_memory(request: Request, client_id: str | None = None) -> dict[str, Any]:
    """전역 + 해당 고객 승인 매핑 목록(전문가 검토용)."""
    items = load_parsing_memory(get_session_factory(request), client_id)
    return {"client_id": client_id, "count": len(items), "items": items}


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


@router.post("/{draft_id}/demo_publish")
def demo_publish(draft_id: int, request: Request) -> dict[str, Any]:
    """데모 전용 — 승인 직후 4-Agent 분석+발행을 **동기**로 끝까지 진행(canned 즉시).

    조종석의 '최종 승인' 한 번으로 사장님 리포트가 발행되어 바로 열람 가능하도록 하는 편의 경로다.
    DEMO_MODE 가 아니면 404(운영은 HITL 검수 흐름을 우회하지 않는다).
    """
    from web.demo import is_demo_mode

    if not is_demo_mode():
        raise HTTPException(status_code=404, detail="demo_publish 는 DEMO_MODE 에서만 제공됩니다")
    store = get_store(request)
    require_draft(store, draft_id)
    orch = get_orchestrator(request)
    orch.run_analysis(draft_id)                  # computed → … → review_pending (Agent 1·2·3)
    # Agent 4(최종본·dashboard_payload)는 피드백 단계에서 생성된다 — 데모는 빈 피드백으로 1회 구동.
    orch.submit_feedback(draft_id, {"reviewer": "데모 자동승인", "overall_note": None, "instructions": []})
    orch.approve(draft_id)                        # review_pending → approved
    payload = orch.publish(draft_id)              # approved → published + KB 적재 훅
    return {
        "draft_id": draft_id,
        "status": "published",
        "dashboard_payload": payload["dashboard_payload"],
    }
