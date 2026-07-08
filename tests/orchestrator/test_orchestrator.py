"""오케스트레이터 상태 머신 테스트 — 에이전트 mock 주입, DB 불필요(InMemoryStore).

computed→drafting→review_pending→revising→review_pending→approved→published 전 구간이
흐르고, agent_runs 4건 감사·published/recommendations 저장을 확인한다. 잘못된 전이는
IllegalTransition, 에이전트 예외 시 status=failed 기록을 확인한다.
"""

from __future__ import annotations

import pytest

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from orchestrator import IllegalTransition, InMemoryStore, Orchestrator

FIXED_TS = "2026-07-07T09:00:00+09:00"
DRAFT_ID = 1
CLIENT_ID = 1001
PERIOD = "2025-Q3"


# --- fake 에이전트 (스키마·guard 우회, 오케스트레이션 배선만 검증) ---
def _fake_pl(**kwargs):
    return {"agent": "pl_analyst", "summary": "PL mock"}


def _fake_bs(**kwargs):
    return {"agent": "bs_analyst", "summary": "BS mock"}


def _fake_report(**kwargs):
    return {
        "agent": "report_master",
        "sections": [{"id": "overview", "title": "요약", "body_md": "초안"}],
        "contradiction_flags": [],
        "cited_values": [],
    }


def _fake_final(**kwargs):
    return {
        "agent": "final_publisher",
        "version": 2,
        "sections": [{"id": "overview", "title": "요약", "body_md": "최종"}],
        "applied_feedback": [],
        "dashboard_payload": {
            "client": {"name": "테스트", "period": "2025년 3분기"},
            "hero_kpis": [{"key": "daily_target_qty", "label": "일일 목표", "value": 35}],
            "kpis": [],
            "followup": {"prev_period": None, "items": []},
            "charts": [],
            "report_sections": [],
        },
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "현금 유보",
             "target_metric": "cash_runway_months", "direction": "increase"},
        ],
    }


def _preloaded_store(status: str = "computed") -> InMemoryStore:
    pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    bs = compute_bs(sample_bs_raw(), pl_context=pl_context_from_pl(pl), computed_at=FIXED_TS)
    store = InMemoryStore()
    store.preload(
        DRAFT_ID, client_id=CLIENT_ID, period=PERIOD,
        financials_pl=pl, financials_bs=bs,
        client_profile=sample_client_profile(),
        followup_context={"is_first_round": True, "current_period": PERIOD},
        status=status,
    )
    return store


def _orch(store: InMemoryStore) -> Orchestrator:
    return Orchestrator(
        store, pl_fn=_fake_pl, bs_fn=_fake_bs, report_fn=_fake_report, publish_fn=_fake_final,
    )


def test_full_pipeline_to_published():
    store = _preloaded_store()
    orch = _orch(store)

    orch.run_analysis(DRAFT_ID)
    assert store.get_draft(DRAFT_ID)["status"] == "review_pending"

    orch.submit_feedback(DRAFT_ID, {"reviewer": "전문가", "instructions": []})
    assert store.get_draft(DRAFT_ID)["status"] == "review_pending"
    assert store.get_draft(DRAFT_ID)["version"] == 2

    orch.approve(DRAFT_ID)
    assert store.get_draft(DRAFT_ID)["status"] == "approved"

    published = orch.publish(DRAFT_ID)
    assert store.get_draft(DRAFT_ID)["status"] == "published"

    # 감사 로그 4건(pl, bs, report, final), 모두 통과
    assert len(store.agent_runs) == 4
    assert [r["agent"] for r in store.agent_runs] == [
        "pl_analyst", "bs_analyst", "report_master", "final_publisher",
    ]
    assert all(r["validation_passed"] for r in store.agent_runs)
    assert all(r["input_hash"] and r["output_hash"] for r in store.agent_runs)

    # 발행본·권고 영속화
    assert DRAFT_ID in store.published
    assert published["dashboard_payload"]["hero_kpis"][0]["value"] == 35
    recs = store.recommendations[DRAFT_ID]
    assert recs[0]["client_id"] == CLIENT_ID and recs[0]["period"] == PERIOD


def test_illegal_transition_rejected():
    """computed 에서 곧바로 approve 시도 → IllegalTransition."""
    store = _preloaded_store()
    orch = _orch(store)
    with pytest.raises(IllegalTransition):
        orch.approve(DRAFT_ID)


def test_publish_without_final_rejected():
    """Agent 4 산출(최종본) 없이 approved 만으로 publish → 거부."""
    store = _preloaded_store()
    orch = _orch(store)
    orch.run_analysis(DRAFT_ID)          # payload = a3 초안(대시보드 없음)
    orch.approve(DRAFT_ID)               # review_pending → approved (허용)
    with pytest.raises(IllegalTransition):
        orch.publish(DRAFT_ID)


def test_agent_failure_marks_failed():
    """에이전트 예외 시 status=failed 기록 + 예외 전파 + agent_run(validation_passed=False)."""
    def _boom(**kwargs):
        raise RuntimeError("model down")

    store = _preloaded_store()
    orch = Orchestrator(
        store, pl_fn=_boom, bs_fn=_fake_bs, report_fn=_fake_report, publish_fn=_fake_final,
    )
    with pytest.raises(RuntimeError):
        orch.run_analysis(DRAFT_ID)
    assert store.get_draft(DRAFT_ID)["status"] == "failed"
    assert store.agent_runs[-1]["validation_passed"] is False
    assert store.agent_runs[-1]["output_hash"] is None
