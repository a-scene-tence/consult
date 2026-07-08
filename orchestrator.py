"""오케스트레이터 — HITL 상태 머신·에이전트 파이프라인 제어 (SPEC §3.1, CLAUDE.md §2.2).

Strict Rule 강제와 HITL 중단/재개를 위해 상태를 영속화하고, 각 전이를 명시적 함수로 표현한다.
LangGraph 미사용(커스텀 Python). 상태는 `StateStore` 프로토콜로 추상화하여, 테스트는 라이브
Postgres 없이 `InMemoryStore` 로 전 구간을 검증할 수 있다(SqlAlchemy 백엔드는 후속 — 모델은 `db/models.py` 존재).

상태 전이(SPEC §3.1):
  computed → drafting → review_pending → revising → (review_pending | approved) → published
  (오류 시 어느 상태에서든 → failed)

각 에이전트 호출은 `agent_runs` 에 입력/출력 해시(sha256)·모델·지연(ms)·검증 통과 여부를 감사 기록한다.
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


class IllegalTransition(Exception):
    """허용되지 않은 상태 전이를 시도했을 때 발생."""


class StateStore(Protocol):
    """오케스트레이터가 의존하는 영속화 계약(Postgres/인메모리 공통).

    draft 레코드는 최소한 {status, version, payload, client_id, period} 를 보유한다.
    """

    def load_inputs(self, draft_id: int) -> dict[str, Any]:
        """draft 의 확정 수치·프로필·Follow-up 입력을 로드한다.

        반환: {financials_pl, financials_bs, client_profile, followup_context}.
        """
        ...

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        """draft 레코드({status, version, payload, client_id, period})를 반환."""
        ...

    def set_status(self, draft_id: int, status: str) -> None:
        """draft.status 를 갱신."""
        ...

    def save_payload(self, draft_id: int, payload: dict[str, Any], version: int) -> None:
        """draft.payload / version 을 갱신."""
        ...

    def add_agent_run(self, record: dict[str, Any]) -> None:
        """agent_runs 감사 로그 1건 추가."""
        ...

    def save_published(self, draft_id: int, dashboard_payload: dict[str, Any]) -> None:
        """발행본(대시보드 페이로드)을 영속화."""
        ...

    def save_recommendations(
        self, draft_id: int, client_id: int, period: str, recommendations: list[dict[str, Any]]
    ) -> None:
        """회차 권고를 구조화 저장(다음 회차 Follow-up 기준)."""
        ...


class InMemoryStore:
    """테스트/개발용 인메모리 StateStore 구현.

    라이브 Postgres 없이 전 상태 전이를 검증한다. 실제 배포는 SqlAlchemy 백엔드로 교체.
    """

    def __init__(self) -> None:
        self.drafts: dict[int, dict[str, Any]] = {}
        self.inputs: dict[int, dict[str, Any]] = {}
        self.agent_runs: list[dict[str, Any]] = []
        self.published: dict[int, dict[str, Any]] = {}
        self.recommendations: dict[int, list[dict[str, Any]]] = {}

    def preload(
        self,
        draft_id: int,
        *,
        client_id: int,
        period: str,
        financials_pl: dict[str, Any],
        financials_bs: dict[str, Any],
        client_profile: dict[str, Any] | None = None,
        followup_context: dict[str, Any] | None = None,
        status: str = "computed",
    ) -> None:
        """compute 완료(status=computed) 상태의 draft 와 입력을 적재한다."""
        self.drafts[draft_id] = {
            "status": status,
            "version": 1,
            "payload": None,
            "client_id": client_id,
            "period": period,
        }
        self.inputs[draft_id] = {
            "financials_pl": financials_pl,
            "financials_bs": financials_bs,
            "client_profile": client_profile,
            "followup_context": followup_context,
        }

    def load_inputs(self, draft_id: int) -> dict[str, Any]:
        return self.inputs[draft_id]

    def get_draft(self, draft_id: int) -> dict[str, Any]:
        return self.drafts[draft_id]

    def set_status(self, draft_id: int, status: str) -> None:
        self.drafts[draft_id]["status"] = status

    def save_payload(self, draft_id: int, payload: dict[str, Any], version: int) -> None:
        self.drafts[draft_id]["payload"] = payload
        self.drafts[draft_id]["version"] = version

    def add_agent_run(self, record: dict[str, Any]) -> None:
        self.agent_runs.append(record)

    def save_published(self, draft_id: int, dashboard_payload: dict[str, Any]) -> None:
        self.published[draft_id] = dashboard_payload

    def save_recommendations(
        self, draft_id: int, client_id: int, period: str, recommendations: list[dict[str, Any]]
    ) -> None:
        self.recommendations[draft_id] = [
            {**rec, "client_id": client_id, "period": period} for rec in recommendations
        ]


def _sha256(obj: Any) -> str:
    """직렬화 가능한 객체의 안정적 sha256 해시(감사 로그용, PII 원문 미기록)."""
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


class Orchestrator:
    """상태 머신 + 에이전트 파이프라인 실행기.

    에이전트 함수는 주입 가능(테스트 mock). 기본값은 실제 Agent 1~4 구현이다.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        pl_fn: Any = analyze_pl,
        bs_fn: Any = analyze_bs,
        report_fn: Any = analyze_report,
        publish_fn: Any = analyze_final,
        client: Any | None = None,
    ) -> None:
        self.store = store
        self.pl_fn = pl_fn
        self.bs_fn = bs_fn
        self.report_fn = report_fn
        self.publish_fn = publish_fn
        self.client = client

    # --- 전이 헬퍼 ---
    def _transition(self, draft_id: int, to_status: str) -> None:
        """허용 전이만 통과시킨다. 위반 시 IllegalTransition."""
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
        """에이전트 호출을 감사 로그로 래핑한다.

        예외 발생 시 draft.status=failed 기록 후 재-raise(스키마·가드 위반 포함).
        `agent_inputs` 는 해시 대상 입력(원문 미기록), `call_kwargs` 는 fn 에 전달할 인자.
        """
        started = time.monotonic()
        try:
            output = fn(**call_kwargs, client=self.client)
        except Exception:
            self.store.add_agent_run(
                {
                    "draft_id": draft_id,
                    "agent": agent_name,
                    "input_hash": _sha256(agent_inputs),
                    "output_hash": None,
                    "model": _resolve_model(),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "validation_passed": False,
                }
            )
            self._mark_failed(draft_id)
            raise
        self.store.add_agent_run(
            {
                "draft_id": draft_id,
                "agent": agent_name,
                "input_hash": _sha256(agent_inputs),
                "output_hash": _sha256(output),
                "model": _resolve_model(),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "validation_passed": True,
            }
        )
        return output

    # --- 상태 전이 진입점 ---
    def run_analysis(self, draft_id: int) -> dict[str, Any]:
        """computed → drafting: Agent 1·2·3 실행 → 초안 저장 → review_pending."""
        self._transition(draft_id, "drafting")
        inputs = self.store.load_inputs(draft_id)
        fin_pl = inputs["financials_pl"]
        fin_bs = inputs["financials_bs"]
        profile = inputs.get("client_profile")
        followup = inputs.get("followup_context")

        # Agent 1·2 는 상호 독립 — 병렬 호출 가능(현재는 순차). Agent 3 은 둘을 기다린다.
        a1 = self._run_agent(
            "pl_analyst", draft_id, self.pl_fn, {"kind": "pl_analysis"},
            financials_pl=fin_pl, client_profile=profile, followup_context=followup,
        )
        a2 = self._run_agent(
            "bs_analyst", draft_id, self.bs_fn, {"kind": "bs_analysis"},
            financials_bs=fin_bs, client_profile=profile, followup_context=followup,
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
        """approved → published: dashboard_payload·recommendations 영속화.

        발행 대상은 Agent 4 산출(agent4_final_report)이어야 한다 — 대시보드/권고가 없으면 실패.
        """
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
        return payload
