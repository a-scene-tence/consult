"""마스킹 v2 테스트 (CLAUDE.md §3.5) — PII Leak 방어 검증.

고의로 상호명·정확 나이·정확 위치·정확 금액을 주입했을 때 산출 레코드에서 완전히
삭제/밴드화되는지(assert_no_pii 통과), 반대로 잔존 시 PiiLeak 이 발생하는지 검증한다.
"""

from __future__ import annotations

import pytest

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from rag.masking import (
    PiiLeak,
    assert_no_pii,
    build_past_case,
    generalize_period,
    mask_amounts_in_text,
    pct_band,
    ratio_band,
    runway_band,
)

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _financials():
    pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    bs = compute_bs(sample_bs_raw(), pl_context=pl_context_from_pl(pl), computed_at=FIXED_TS)
    return pl, bs


def _published_with_pii():
    """상호명·정확 금액을 본문에 심은 발행본(마스킹 대상)."""
    return {
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "○○치킨 역삼점은 보증금 1,200만 원 부담이 크고 월세 300만원이 나갑니다."},
        ],
        "applied_feedback": [
            {"from": "세무 파트너", "directive_ref": "overall_note",
             "how_applied": "역삼동 상권 특성상 현금 유보를 강조", "scope": "global"},
        ],
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "세금 500만원 유보 계좌 분리",
             "target_metric": "cash_runway_months", "direction": "increase"},
        ],
    }


# --- 밴드 헬퍼 ---
def test_band_helpers():
    assert pct_band(14.2) == "10-15%"
    assert ratio_band(122.1) == "120-140%"
    assert runway_band(2.7) == "2-3개월"
    assert generalize_period("2025-Q3") == "2025-하반기"
    assert generalize_period("2025-Q1") == "2025-상반기"


def test_mask_amounts_blinds_exact_figures():
    out = mask_amounts_in_text("보증금 1,200만 원과 월세 300만원")
    assert "1,200" not in out and "300만원" not in out
    assert "○○" in out


# --- 레코드 빌더: PII 완전 제거 ---
def test_build_past_case_strips_pii():
    pl, bs = _financials()
    profile = sample_client_profile()  # trade_name ○○치킨 역삼점, owner_age 34, location 역삼동
    record = build_past_case(
        _published_with_pii(), {"overall_note": "부드럽게"}, profile, pl, bs,
        case_id="K-000108",
    )
    # 코호트 밴드만 존재, 상호/정확위치/정확나이/client_id 부재
    assert record["cohort_meta"]["owner_age_band"] == "30대 초중반"
    assert record["cohort_meta"]["district_type"] == "office"
    assert "owner_age" not in record["cohort_meta"]
    assert record["financial_profile"]["OPM_band"] == "10-15%"
    assert record["masking_version"] == "v2"
    # 이중 방어 스캔 통과(상호/위치/나이/금액 잔존 없음)
    assert_no_pii(
        record, trade_name=profile["trade_name"],
        location_raw=profile["location_raw"], owner_age=profile["owner_age"],
    )


# --- 누출 케이스: PiiLeak 발생 ---
def test_leak_trade_name_detected():
    with pytest.raises(PiiLeak):
        assert_no_pii({"summary": "○○치킨 역삼점 사례"}, trade_name="○○치킨 역삼점")


def test_leak_exact_age_detected():
    with pytest.raises(PiiLeak):
        assert_no_pii({"summary": "대표 34세 기준 분석"}, owner_age=34)


def test_leak_exact_location_detected():
    with pytest.raises(PiiLeak):
        assert_no_pii({"summary": "서울 강남구 역삼동 소재"}, location_raw="서울 강남구 역삼동")


def test_leak_exact_amount_detected():
    with pytest.raises(PiiLeak):
        assert_no_pii({"summary": "보증금 1,200만 원 부담"})


def test_leak_forbidden_key_detected():
    with pytest.raises(PiiLeak):
        assert_no_pii({"owner_age": 34, "summary": "ok"})
