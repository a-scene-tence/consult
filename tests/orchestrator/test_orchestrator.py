"""오케스트레이터 DB 영속화 통합 테스트 — SqlAlchemyStore + SQLite in-memory.

각 상태 전이가 실제 DB 트랜잭션으로 커밋되는지(매 단계 DB 재조회), agent_runs 4행,
published_reports·recommendations 행, publish 직후 kb_ingestions success 를 검증한다. 잘못된
전이는 IllegalTransition, 에이전트 예외 시 status=failed 커밋을 확인한다. 에이전트·RAG 는 mock.
"""

from __future__ import annotations

import pytest

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from db.models import (
    AgentRun,
    Client,
    Financials,
    KbIngestion,
    PublishedReport,
    Recommendation,
    ReportDraft,
)
from db.session import create_all, make_engine, make_session_factory
from orchestrator import IllegalTransition, Orchestrator, SqlAlchemyStore
from rag.chroma_client import InMemoryCaseStore

FIXED_TS = "2026-07-07T09:00:00+09:00"
PERIOD = "2025-Q3"


# --- fake 에이전트(스키마·guard 우회, 오케스트레이션 배선만 검증) ---
def _fake_pl(**kwargs):
    return {"agent": "pl_analyst", "summary": "PL mock"}


def _fake_bs(**kwargs):
    return {"agent": "bs_analyst", "summary": "BS mock"}


def _fake_report(**kwargs):
    return {
        "agent": "report_master",
        "sections": [{"id": "overview", "title": "요약", "body_md": "초안"}],
        "contradiction_flags": [], "cited_values": [],
    }


def _fake_final(**kwargs):
    return {
        "agent": "final_publisher", "version": 2,
        "sections": [{"id": "overview", "title": "요약", "body_md": "하루 35개면 손익분기"}],
        "applied_feedback": [],
        "dashboard_payload": {
            "client": {"name": "테스트", "period": "2025년 3분기"},
            "hero_kpis": [{"key": "daily_target_qty", "label": "일일 목표", "value": 35}],
            "kpis": [], "followup": {"prev_period": None, "items": []},
            "charts": [], "report_sections": [],
        },
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "현금 유보",
             "target_metric": "cash_runway_months", "direction": "increase"},
        ],
    }


@pytest.fixture()
def seeded():
    """SQLite in-memory 에 client·financials·draft(computed) 를 적재하고 (store, draft_id) 반환."""
    engine = make_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    sf = make_session_factory(engine)
    pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    bs = compute_bs(sample_bs_raw(), pl_context=pl_context_from_pl(pl), computed_at=FIXED_TS)
    profile = sample_client_profile()
    with sf() as s:
        c = Client(name="사장님", **{k: profile[k] for k in (
            "trade_name", "industry", "district_type", "location_raw",
            "owner_gender", "owner_age", "owner_age_band", "risk_appetite")})
        s.add(c); s.flush()
        cid = c.id
        s.add(Financials(client_id=cid, period=PERIOD, kind="pl", payload_json=pl))
        s.add(Financials(client_id=cid, period=PERIOD, kind="bs", payload_json=bs))
        d = ReportDraft(client_id=cid, period=PERIOD, version=1, status="computed")
        s.add(d); s.flush()
        did = d.id
        s.commit()
    return SqlAlchemyStore(sf), did, sf


def _orch(store, **overrides):
    kwargs = dict(
        pl_fn=_fake_pl, bs_fn=_fake_bs, report_fn=_fake_report, publish_fn=_fake_final,
        case_store=InMemoryCaseStore(),
    )
    kwargs.update(overrides)
    return Orchestrator(store, **kwargs)


def test_full_pipeline_persists_to_published(seeded):
    store, did, sf = seeded
    orch = _orch(store)

    orch.run_analysis(did)
    assert store.get_draft(did)["status"] == "review_pending"  # DB 재조회

    orch.submit_feedback(did, {"reviewer": "전문가", "overall_note": "부드럽게", "instructions": []})
    assert store.get_draft(did)["status"] == "review_pending"
    assert store.get_draft(did)["version"] == 2

    orch.approve(did)
    assert store.get_draft(did)["status"] == "approved"

    orch.publish(did)
    assert store.get_draft(did)["status"] == "published"

    with sf() as s:
        runs = s.query(AgentRun).order_by(AgentRun.id).all()
        assert [r.agent for r in runs] == [
            "pl_analyst", "bs_analyst", "report_master", "final_publisher"]
        assert all(r.validation_passed for r in runs)
        assert s.query(PublishedReport).count() == 1
        recs = s.query(Recommendation).all()
        assert len(recs) == 1 and recs[0].rec_code == "R-2025Q3-01"
        assert recs[0].client_id is not None and recs[0].period == PERIOD
        # publish 직후 KB 적재(비차단 훅) → kb_ingestions success
        kb = s.query(KbIngestion).all()
        assert len(kb) == 1 and kb[0].status == "success"


def test_illegal_transition_rejected(seeded):
    store, did, _ = seeded
    orch = _orch(store)
    with pytest.raises(IllegalTransition):
        orch.approve(did)  # computed → approved 불가


def test_publish_without_final_rejected(seeded):
    store, did, _ = seeded
    orch = _orch(store)
    orch.run_analysis(did)   # payload = a3 초안(대시보드 없음)
    orch.approve(did)        # review_pending → approved
    with pytest.raises(IllegalTransition):
        orch.publish(did)


def test_agent_failure_marks_failed(seeded):
    store, did, sf = seeded

    def _boom(**kwargs):
        raise RuntimeError("model down")

    orch = _orch(store, pl_fn=_boom)
    with pytest.raises(RuntimeError):
        orch.run_analysis(did)
    assert store.get_draft(did)["status"] == "failed"  # 트랜잭션 커밋됨
    with sf() as s:
        last = s.query(AgentRun).order_by(AgentRun.id.desc()).first()
        assert last.validation_passed is False and last.output_hash is None
