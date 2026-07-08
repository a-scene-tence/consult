"""numeric_guard v0.3 — 확장 Golden Set(financials + profile + followup) 회귀 테스트.

핵심: 가짜 프로필 나이("99세")·가짜 마진율("12.34%")을 반드시 적발(False)하고,
실제 프로필 나이·metric_progress 수치는 통과시킨다.
"""

from __future__ import annotations

from compute.compute_pl import compute_pl
from compute.ingest import sample_client_profile, sample_pl_raw
from guards.numeric_guard import (
    find_hallucinated_numbers,
    golden_sources,
    numeric_guard,
)

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _fixtures():
    fin_pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    profile = sample_client_profile()  # owner_age = 34
    followup = {
        "is_first_round": False,
        "current_period": "2025-Q3",
        "prev_period": "2025-Q2",
        "previous_recommendations": [
            {
                "rec_code": "R-2025Q2-01",
                "text": "매출채권 회수기일 단축",
                "target_metric": "ar_days",
                "direction": "decrease",
                "status": "in_progress",
            }
        ],
        "metric_progress": [
            {"code": "ar_days", "prev_value": 42.0, "value": 33.0, "direction": "improved"},
            {"code": "DR", "prev_value_pct": 137.5, "value_pct": 122.1, "direction": "improved"},
        ],
    }
    return golden_sources(fin_pl, profile, followup)


def test_golden_includes_profile_age_and_metric_progress():
    """프로필 나이(34)·metric_progress 수치(42.0/33.0/137.5/122.1)가 Golden Set에 포함."""
    sources = _fixtures()
    output = {
        "agent": "pl_analyst",
        "summary": "대표자 34세. 회수기일 42.0에서 33.0으로, 부채비율 137.5에서 122.1로 개선.",
    }
    assert numeric_guard(sources, output) is True


def test_fake_profile_age_and_margin_detected():
    """[필수] 가짜 나이 '99세'와 가짜 마진율 '12.34%' 주입 → 적발(False)."""
    sources = _fixtures()
    output = {
        "agent": "pl_analyst",
        "findings": [
            {"text": "대표자 99세 기준 분석.", "impact": "neutral"},
            {"text": "이 품목 마진율은 12.34%로 산출됐다.", "impact": "positive"},
        ],
    }
    violations = find_hallucinated_numbers(sources, output)
    assert numeric_guard(sources, output) is False
    assert "99" in violations
    assert "12.34" in violations


def test_valid_agent1_output_passes():
    """실제 확정 수치만 인용한 정상 출력은 통과."""
    sources = _fixtures()
    output = {
        "agent": "pl_analyst",
        "summary": "영업이익률 14.2%로 개선.",
        "findings": [
            {"topic": "item_margin", "text": "후라이드치킨 마진율 60.0%로 효자.",
             "source_ref": ["후라이드치킨"], "impact": "positive"},
        ],
        "bep_guidance": {
            "text": "손익분기 매출은 48,501,789원입니다.",
            "source_ref": ["bep.bep_revenue"],
        },
        "tax_risk_alerts": [
            {"text": "현금 매출 7.0% 미매칭.", "source_ref": ["unallocated_cash_sales"], "severity": "medium"},
        ],
        "adherence_review": [
            {"rec_code": "R-2025Q2-01", "text": "회수기일 개선.", "source_ref": ["metric_progress.ar_days"]},
        ],
        "milestone_review": [],
        "out_of_scope": ["부채/현금흐름은 본 분석 범위 아님"],
    }
    assert numeric_guard(sources, output) is True


def test_exemptions_no_false_positive():
    """연도·분기·코드·카운팅 단위는 오탐하지 않는다."""
    sources = _fixtures()
    output = {"text": "2025년 2분기 R-2025Q2-01 항목, 매장 3개 대상."}
    assert numeric_guard(sources, output) is True


def test_comma_and_percent_formatting():
    """쉼표·퍼센트 포맷은 값으로 정규화되어 오탐하지 않는다."""
    sources = _fixtures()
    output = {"text": "매출 130,000,000원, 영업이익률 14.2%."}
    assert numeric_guard(sources, output) is True


def test_golden_sources_helper_drops_none():
    fin_pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    assert golden_sources(fin_pl) == [fin_pl]
    assert len(golden_sources(fin_pl, sample_client_profile(), None)) == 2
