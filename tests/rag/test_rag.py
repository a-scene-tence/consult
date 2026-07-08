"""RAG 코호트 조회·적재 테스트 (CLAUDE.md §2.4, SPEC §1.4).

InMemoryCaseStore 로 (a) 정확 코호트 필터 매칭, (b) 희소 코호트 완화 Fallback, (c) 임계값 컷,
(d) retrieve_rag_context 의 주입 전 PII 드롭, (e) index_published_case 적재·멱등·kb_ingestions
감사를 검증한다(chromadb 불필요, DB는 SQLite in-memory).
"""

from __future__ import annotations

import pytest

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from db.models import Client, KbIngestion, ReportDraft
from db.session import create_all, make_engine, make_session_factory
from rag.case_indexer import _store_metadata, index_published_case, retrieve_rag_context
from rag.chroma_client import InMemoryCaseStore, retrieve_cases

FIXED_TS = "2026-07-07T09:00:00+09:00"

_COHORT = {
    "industry": "치킨전문점", "district_type": "office",
    "owner_gender": "female", "owner_age_band": "30대 초중반", "risk_appetite": "conservative",
}


def _financials():
    pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    bs = compute_bs(sample_bs_raw(), pl_context=pl_context_from_pl(pl), computed_at=FIXED_TS)
    return pl, bs


def _record(case_id, cohort, summary, lessons, outcome="success"):
    return {
        "case_id": case_id, "cohort_meta": cohort, "period_generalized": "2025-하반기",
        "financial_profile": {"OPM_band": "10-15%", "DR_band": "120-140%", "runway_band": "2-3개월"},
        "situation_summary": summary, "expert_lessons": lessons,
        "outcome_label": outcome, "masking_version": "v2",
    }


def _seed(store, case_id, cohort, summary, lessons=("점심 세트 유효",)):
    rec = _record(case_id, cohort, summary, list(lessons))
    store.upsert(case_id, summary + " " + " ".join(lessons), _store_metadata(rec))


# --- (a) 정확 코호트 필터 ---
def test_exact_cohort_match():
    store = InMemoryCaseStore()
    _seed(store, "K1", _COHORT, "오피스 상권 치킨집 배달 급증")
    _seed(store, "K2", {**_COHORT, "industry": "카페"}, "카페 사례")  # 업종 불일치 → 제외
    cases, level, relaxed = retrieve_cases(store, _COHORT, "오피스 상권 치킨집 배달 급증", min_similarity=0.3)
    assert [c["case_id"] for c in cases] == ["K1"]
    assert level == 0 and relaxed == []


# --- (b) 희소 코호트 → 완화 Fallback ---
def test_relaxation_fallback():
    store = InMemoryCaseStore()
    # 성향·성별이 다른 케이스만 존재 → risk_appetite→owner_gender 완화 후 매칭.
    _seed(store, "K9", {**_COHORT, "risk_appetite": "aggressive", "owner_gender": "male"},
          "오피스 상권 치킨집 배달 급증")
    cases, level, relaxed = retrieve_cases(store, _COHORT, "오피스 상권 치킨집 배달 급증", min_similarity=0.3)
    assert [c["case_id"] for c in cases] == ["K9"]
    assert level == 2
    assert relaxed == ["risk_appetite", "owner_gender"]


# --- (c) 임계값 미달 → 빈 결과 ---
def test_similarity_threshold_cut():
    store = InMemoryCaseStore()
    _seed(store, "K1", _COHORT, "전혀 무관한 내용")
    cases, level, relaxed = retrieve_cases(
        store, _COHORT, "오피스 상권 치킨집 배달수수료 급증 국면", min_similarity=0.95
    )
    assert cases == []


# --- (d) retrieve_rag_context: 정상 조립 + 주입 전 PII 드롭 ---
def test_retrieve_rag_context_assembles_and_validates():
    pl, bs = _financials()
    store = InMemoryCaseStore()
    _seed(store, "K1", _COHORT, "오피스 상권 치킨집, 배달수수료 급증 국면.",
          lessons=["점심 오피스 세트 도입이 공헌이익 개선에 유효"])
    ctx = retrieve_rag_context(
        store, sample_client_profile(), pl, bs, min_similarity=0.0, now="2026-07-08T00:00:00+00:00"
    )
    assert ctx is not None
    assert ctx["query"]["industry"] == "치킨전문점"
    assert ctx["query"]["relaxation_level"] == 0
    assert ctx["cases"][0]["case_id"] == "K1"
    assert ctx["cases"][0]["expert_lessons"]


def test_retrieve_rag_context_drops_leaked_case():
    """상황 요약에 정확 금액이 남은 케이스는 주입 전 스캔에서 드롭된다."""
    pl, bs = _financials()
    store = InMemoryCaseStore()
    _seed(store, "KLEAK", _COHORT, "보증금 1,200만 원 부담이 큰 오피스 치킨집.")
    ctx = retrieve_rag_context(store, sample_client_profile(), pl, bs, min_similarity=0.0)
    assert ctx is None  # 유일 케이스가 드롭 → 빈 컨텍스트


# --- (e) 적재 + 멱등 + kb_ingestions 감사 ---
@pytest.fixture()
def db_factory():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    return make_session_factory(engine)


def _seed_draft(session_factory, profile):
    with session_factory() as s:
        c = Client(name="사장님", **{k: profile[k] for k in (
            "trade_name", "industry", "district_type", "location_raw",
            "owner_gender", "owner_age", "owner_age_band", "risk_appetite")})
        s.add(c); s.flush()
        d = ReportDraft(client_id=c.id, period="2025-Q3", version=1, status="published")
        s.add(d); s.flush()
        did = d.id
        s.commit()
    return did


def test_index_published_case_and_idempotency(db_factory):
    pl, bs = _financials()
    profile = sample_client_profile()
    did = _seed_draft(db_factory, profile)
    store = InMemoryCaseStore()
    published = {
        "sections": [{"id": "o", "title": "요약", "body_md": "하루 35개면 손익분기."}],
        "applied_feedback": [{"how_applied": "현금 유보 강조"}],
        "recommendations": [{"text": "세금 유보 계좌 분리"}],
    }
    case_id = index_published_case(
        store, db_factory, did, published=published, expert_feedback={"overall_note": "부드럽게"},
        client_profile=profile, financials_pl=pl, financials_bs=bs,
    )
    assert case_id == f"K-{did:06d}"
    with db_factory() as s:
        rows = s.query(KbIngestion).all()
        assert len(rows) == 1 and rows[0].status == "success" and rows[0].masking_version == "v2"
    # 멱등: 재호출해도 새 적재/새 kb 행 없음
    again = index_published_case(
        store, db_factory, did, published=published, expert_feedback=None,
        client_profile=profile, financials_pl=pl, financials_bs=bs,
    )
    assert again == case_id
    with db_factory() as s:
        assert s.query(KbIngestion).count() == 1
