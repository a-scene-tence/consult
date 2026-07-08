"""오케스트레이터 — HITL 상태 머신·에이전트 파이프라인 제어 (SPEC §3.1, CLAUDE.md §2.1·§2.2).

Strict Rule 강제와 HITL 중단/재개를 위해 상태를 **PostgreSQL에 영속화**하고, 각 전이를 명시적
함수로 표현한다(LangGraph 미사용). 상태 접근은 `StateStore` 프로토콜로 추상화하며, 운영은
`SqlAlchemyStore`(각 전이 = DB 트랜잭션), 테스트는 동일 모델을 SQLite in-memory 로 구동한다.

상태 전이(SPEC §3.1):
  computed → drafting → review_pending → revising → (review_pending | approved) → published
  (오류 시 어느 상태에서든 → failed)

지속 학습(SPEC §1.4): Agent 1·2 호출 **전** 코호트 조회로 `rag_context`를 조립해 주입하고,
`published` 직후 **비차단 격리 훅**으로 발행 자산을 마스킹·적재한다(실패해도 발행은 성공).
각 에이전트 호출은 `agent_runs`에 입력/출력 해시(sha256)·모델·지연(ms)·검증 통과 여부를 감사 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Protocol

from agents.base import DEFAULT_MODEL
from agents.bs_analyst import analyze_bs
from agents.final_publisher import analyze_final
from agents.pl_analyst import analyze_pl
from agents.report_master import analyze_report
from compute.compute_metrics import (
    compute_metric_progress,
    extract_bs_metrics,
    extract_pl_metrics,
)
from db.models import (
    AgentRun,
    Client,
    ExpertFeedback,
    Financials,
    PublishedReport,
    Recommendation,
    ReportDraft,
)
from rag.case_indexer import index_published_case, retrieve_rag_context

# SPEC §3.1 허용 전이. failed 는 어느 상태에서든 진입 가능(_mark_failed 로 직접 처리).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "computed": frozenset({"drafting"}),
    "drafting": frozenset({"review_pending", "failed"}),
    "review_pending": frozenset({"revising", "approved"}),
    "revising": frozenset({"review_pending", "approved", "failed"}),
    "approved": frozenset({"published"}),
    "published": frozenset(),
    "failed": frozenset({"drafting"}),
}

# client_profile 로 노출할 clients 컬럼(에이전트·마스킹이 사용).
_PROFILE_FIELDS: tuple[str, ...] = (
    "trade_name", "industry", "district_type", "location_raw",
    "owner_gender", "owner_age", "owner_age_band", "risk_appetite",
)


class IllegalTransition(Exception):
    """허용되지 않은 상태 전이를 시도했을 때 발생."""


class StateStore(Protocol):
    """오케스트레이터가 의존하는 영속화 계약."""

    def load_inputs(self, draft_id: int) -> dict[str, Any]: ...
    def get_draft(self, draft_id: int) -> dict[str, Any]: ...
    def set_status(self, draft_id: int, status: str) -> None: ...
    def save_payload(self, draft_id: int, payload: dict[str, Any], version: int) -> None: ...
    def add_agent_run(self, record: dict[str, Any]) -> None: ...
    def save_published(self, draft_id: int, dashboard_payload: dict[str, Any]) -> None: ...
    def save_recommendations(
        self, draft_id: int, client_id: int, period: str, recommendations: list[dict[str, Any]]
    ) -> None: ...


class SqlAlchemyStore:
    """PostgreSQL/SQLite 백엔드 StateStore — 각 메서드가 독립 트랜잭션으로 커밋된다.

    `session_factory` 는 `db.session.make_session_factory` 산출물. RAG 적재 훅도 이 팩토리를
    재사용해 `kb_ingestions` 를 기록한다.
    """

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    # --- 입력 로드 ---
    def _client_profile(self, client: Client) -> dict[str, Any]:
        profile: dict[str, Any] = {"client_id": f"C-{client.id}"}
        for field in _PROFILE_FIELDS:
            profile[field] = getattr(client, field)
        return profile

    def _financials(self, session: Any, client_id: int, period: str, kind: str) -> dict | None:
        row = (
            session.query(Financials)
            .filter_by(client_id=client_id, period=period, kind=kind)
            .order_by(Financials.computed_at.desc())
            .first()
        )
        return row.payload_json if row else None

    def _build_followup(
        self, session: Any, client_id: int, period: str,
        fin_pl: dict[str, Any], fin_bs: dict[str, Any],
    ) -> dict[str, Any]:
        """직전 회차가 있으면 metric_progress 를 조립, 없으면 baseline."""
        prev_period = fin_pl.get("meta", {}).get("prev_period")
        base = {"is_first_round": True, "current_period": period}
        if not prev_period:
            return base
        prev_pl = self._financials(session, client_id, prev_period, "pl")
        prev_bs = self._financials(session, client_id, prev_period, "bs")
        if not (prev_pl and prev_bs):
            return base
        prev_metrics = {**extract_pl_metrics(prev_pl), **extract_bs_metrics(prev_bs)}
        curr_metrics = {**extract_pl_metrics(fin_pl), **extract_bs_metrics(fin_bs)}
        prev_recs = (
            session.query(Recommendation).filter_by(client_id=client_id, period=prev_period).all()
        )
        return {
            "is_first_round": False,
            "current_period": period,
            "prev_period": prev_period,
            "previous_recommendations": [
                {
                    "rec_code": r.rec_code, "text": r.text, "target_metric": r.target_metric,
                    "direction": r.direction, "status": r.status,
                }
                for r in prev_recs
            ],
            "metric_progress": compute_metric_progress(prev_metrics, curr_metrics),
        }

    def load_inputs(self, draft_id: int) -> dict[str, Any]:
        with self.session_factory() as session:
            draft = session.get(ReportDraft, draft_id)
            client = session.get(Client, draft.client_id)
            fin_pl = self._financials(session, draft.client_id, draft.period, "pl")
            fin_bs = self._financials(session, draft.client_id, draft.period, "bs")
            return {
                "financials_pl": fin_pl,
                "financials_bs": fin_bs,
                "client_profile": self._client_profile(client),
                "followup_context": self._build_followup(
                    session, draft.client_id, draft.period, fin_pl, fin_bs
                ),
            }

    # --- draft 상태 ---
    def get_draft(self, draft_id: int) -> dict[str, Any]:
        with self.session_factory() as session:
            d = session.get(ReportDraft, draft_id)
            return {
                "status": d.status, "version": d.version, "payload": d.payload_json,
                "client_id": d.client_id, "period": d.period,
            }

    def set_status(self, draft_id: int, status: str) -> None:
        with self.session_factory() as session:
            session.get(ReportDraft, draft_id).status = status
            session.commit()

    def save_payload(self, draft_id: int, payload: dict[str, Any], version: int) -> None:
        with self.session_factory() as session:
            d = session.get(ReportDraft, draft_id)
            d.payload_json = payload
            d.version = version
            session.commit()

    # --- 감사·발행·권고 ---
    def add_agent_run(self, record: dict[str, Any]) -> None:
        with self.session_factory() as session:
            session.add(AgentRun(**record))
            session.commit()

    def save_published(self, draft_id: int, dashboard_payload: dict[str, Any]) -> None:
        with self.session_factory() as session:
            session.add(
                PublishedReport(draft_id=draft_id, dashboard_payload_json=dashboard_payload)
            )
            session.commit()

    def save_recommendations(
        self, draft_id: int, client_id: int, period: str, recommendations: list[dict[str, Any]]
    ) -> None:
        with self.session_factory() as session:
            for rec in recommendations:
                session.add(
                    Recommendation(
                        draft_id=draft_id, client_id=client_id, period=period,
                        rec_code=rec["rec_code"], text=rec["text"],
                        target_metric=rec.get("target_metric"),
                        direction=rec.get("direction"),
                        status=rec.get("status", "proposed"),
                    )
                )
            session.commit()

    def load_latest_feedback(self, draft_id: int) -> dict[str, Any] | None:
        """KB 적재 훅용 — 가장 최근 전문가 피드백(overall_note 교훈 추출)."""
        with self.session_factory() as session:
            fb = (
                session.query(ExpertFeedback).filter_by(draft_id=draft_id)
                .order_by(ExpertFeedback.created_at.desc()).first()
            )
            if fb is None:
                return None
            return {
                "reviewer": fb.reviewer, "overall_note": fb.overall_note,
                "instructions": fb.instructions_json,
            }


def _sha256(obj: Any) -> str:
    """직렬화 가능한 객체의 안정적 sha256 해시(감사 로그용, PII 원문 미기록)."""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


class Orchestrator:
    """상태 머신 + 에이전트 파이프라인 + 지속 학습 훅 실행기.

    에이전트 함수·RAG 함수·CaseStore 는 주입 가능(테스트 mock). `case_store` 가 None 이면
    RAG 조회/적재는 건너뛴다(우아한 저하).
    """

    def __init__(
        self,
        store: StateStore,
        *,
        pl_fn: Any = analyze_pl,
        bs_fn: Any = analyze_bs,
        report_fn: Any = analyze_report,
        publish_fn: Any = analyze_final,
        case_store: Any | None = None,
        retrieve_fn: Any = retrieve_rag_context,
        index_fn: Any = index_published_case,
        client: Any | None = None,
    ) -> None:
        self.store = store
        self.pl_fn = pl_fn
        self.bs_fn = bs_fn
        self.report_fn = report_fn
        self.publish_fn = publish_fn
        self.case_store = case_store
        self.retrieve_fn = retrieve_fn
        self.index_fn = index_fn
        self.client = client

    # --- 전이 헬퍼 ---
    def _transition(self, draft_id: int, to_status: str) -> None:
        current = self.store.get_draft(draft_id)["status"]
        if to_status not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise IllegalTransition(
                f"draft {draft_id}: '{current}' → '{to_status}' 전이는 허용되지 않습니다."
            )
        self.store.set_status(draft_id, to_status)

    def _mark_failed(self, draft_id: int) -> None:
        """오류 시 어느 상태에서든 failed 로 강제(전이 검증 우회)."""
        self.store.set_status(draft_id, "failed")

    def _run_agent(
        self, agent_name: str, draft_id: int, fn: Any, agent_inputs: Any, /, **call_kwargs: Any
    ) -> dict[str, Any]:
        """에이전트 호출을 감사 로그로 래핑. 예외 시 status=failed 기록 후 재-raise."""
        started = time.monotonic()
        try:
            output = fn(**call_kwargs, client=self.client)
        except Exception:
            self.store.add_agent_run({
                "draft_id": draft_id, "agent": agent_name,
                "input_hash": _sha256(agent_inputs), "output_hash": None,
                "model": _resolve_model(),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "validation_passed": False,
            })
            self._mark_failed(draft_id)
            raise
        self.store.add_agent_run({
            "draft_id": draft_id, "agent": agent_name,
            "input_hash": _sha256(agent_inputs), "output_hash": _sha256(output),
            "model": _resolve_model(),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "validation_passed": True,
        })
        return output

    def _retrieve_rag(
        self, profile: dict[str, Any] | None, fin_pl: dict[str, Any], fin_bs: dict[str, Any]
    ) -> dict[str, Any] | None:
        """코호트 조회로 rag_context 조립(실패·미설정 시 None — 빈 컨텍스트)."""
        if self.case_store is None or not profile:
            return None
        try:
            return self.retrieve_fn(self.case_store, profile, fin_pl, fin_bs)
        except Exception:
            return None  # 조회 실패는 분석을 막지 않는다(빈 컨텍스트로 진행).

    # --- 상태 전이 진입점 ---
    def run_analysis(self, draft_id: int) -> dict[str, Any]:
        """computed → drafting: (RAG 조회) → Agent 1·2·3 실행 → 초안 저장 → review_pending."""
        self._transition(draft_id, "drafting")
        inputs = self.store.load_inputs(draft_id)
        fin_pl = inputs["financials_pl"]
        fin_bs = inputs["financials_bs"]
        profile = inputs.get("client_profile")
        followup = inputs.get("followup_context")
        rag_context = self._retrieve_rag(profile, fin_pl, fin_bs)

        # Agent 1·2 는 상호 독립 — 병렬 호출 가능(현재는 순차). Agent 3 은 둘을 기다린다.
        a1 = self._run_agent(
            "pl_analyst", draft_id, self.pl_fn, {"kind": "pl_analysis"},
            financials_pl=fin_pl, client_profile=profile, followup_context=followup,
            rag_context=rag_context,
        )
        a2 = self._run_agent(
            "bs_analyst", draft_id, self.bs_fn, {"kind": "bs_analysis"},
            financials_bs=fin_bs, client_profile=profile, followup_context=followup,
            rag_context=rag_context,
        )
        a3 = self._run_agent(
            "report_master", draft_id, self.report_fn, {"kind": "draft_report"},
            agent1_output=a1, agent2_output=a2, client_profile=profile,
        )
        draft = self.store.get_draft(draft_id)
        self.store.save_payload(draft_id, a3, draft["version"])
        self._transition(draft_id, "review_pending")
        return a3

    def submit_feedback(self, draft_id: int, expert_feedback: dict[str, Any]) -> dict[str, Any]:
        """review_pending → revising: Agent 4 재작성 → 개정본 저장(version+1) → review_pending."""
        self._transition(draft_id, "revising")
        inputs = self.store.load_inputs(draft_id)
        draft = self.store.get_draft(draft_id)
        a4 = self._run_agent(
            "final_publisher", draft_id, self.publish_fn, {"kind": "final_report"},
            agent3_draft=draft["payload"], expert_feedback=expert_feedback,
            financials_pl=inputs["financials_pl"], financials_bs=inputs["financials_bs"],
            client_profile=inputs.get("client_profile"),
            followup_context=inputs.get("followup_context"),
        )
        self.store.save_payload(draft_id, a4, draft["version"] + 1)
        self._transition(draft_id, "review_pending")
        return a4

    def approve(self, draft_id: int) -> None:
        """review_pending → approved."""
        self._transition(draft_id, "approved")

    def publish(self, draft_id: int) -> dict[str, Any]:
        """approved → published: dashboard_payload·recommendations 영속화 + KB 적재(비차단)."""
        draft = self.store.get_draft(draft_id)
        payload = draft.get("payload") or {}
        if "dashboard_payload" not in payload or "recommendations" not in payload:
            raise IllegalTransition(
                f"draft {draft_id}: 발행할 최종본(Agent 4 산출)이 없습니다 — 전문가 검토(Agent 4)가 필요합니다."
            )
        self._transition(draft_id, "published")
        self.store.save_published(draft_id, payload["dashboard_payload"])
        self.store.save_recommendations(
            draft_id, draft["client_id"], draft["period"], payload["recommendations"]
        )
        self._ingest_case(draft_id, payload)
        return payload

    def _ingest_case(self, draft_id: int, payload: dict[str, Any]) -> None:
        """발행 직후 KB 적재 훅 — **비차단 격리**(실패해도 발행은 이미 성공 커밋됨).

        프로덕션은 워커/스레드로 승격 가능(결정론적 테스트 유지 위해 여기서는 동기 호출).
        멱등성·kb_ingestions 감사·PII 검증은 index_fn(case_indexer)이 담당한다.
        """
        if self.case_store is None or self.index_fn is None:
            return
        try:
            inputs = self.store.load_inputs(draft_id)
            feedback = None
            if hasattr(self.store, "load_latest_feedback"):
                feedback = self.store.load_latest_feedback(draft_id)
            self.index_fn(
                self.case_store, self.store.session_factory, draft_id,
                published=payload, expert_feedback=feedback,
                client_profile=inputs["client_profile"],
                financials_pl=inputs["financials_pl"], financials_bs=inputs["financials_bs"],
            )
        except Exception:
            # 비차단: 발행 응답을 막지 않는다. 실패는 kb_ingestions 에 failed 로 남는다.
            pass
