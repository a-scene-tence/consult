"""Agent 1·2 v0.3 파이프라인 테스트 — API 모킹.

Fake Anthropic client(tool_use 블록 반환)를 주입해 4종 컨텍스트를 받은 analyze_pl/analyze_bs가
스키마 검증 + numeric_guard 를 통과하는지, 환각·스키마 위반이 각각 예외를 내는지 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.bs_analyst import analyze_bs
from agents.pl_analyst import analyze_pl
from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import (
    sample_bs_raw,
    sample_client_profile,
    sample_pl_raw,
)
from guards.numeric_guard import NumericGuardViolation

FIXED_TS = "2026-07-07T09:00:00+09:00"


class _FakeClient:
    """messages.create 가 지정 output 을 tool_use 블록으로 반환하는 스텁."""

    def __init__(self, output: dict):
        self._output = output
        self.messages = self

    def create(self, **kwargs):
        tool_name = kwargs["tools"][0]["name"]
        block = SimpleNamespace(type="tool_use", name=tool_name, input=self._output)
        return SimpleNamespace(content=[block])


def _financials_pl():
    return compute_pl(sample_pl_raw(), computed_at=FIXED_TS)


def _financials_bs():
    pl = _financials_pl()
    return compute_bs(sample_bs_raw(), pl_context=pl_context_from_pl(pl), computed_at=FIXED_TS)


def _followup():
    return {
        "is_first_round": False,
        "current_period": "2025-Q3",
        "prev_period": "2025-Q2",
        "previous_recommendations": [],
        "metric_progress": [
            {"code": "OPM", "prev_value_pct": 10.0, "value_pct": 14.2, "direction": "improved"},
        ],
    }


def _valid_pl_output():
    return {
        "agent": "pl_analyst",
        "summary": "영업이익률 14.2%로 개선.",
        "findings": [
            {"topic": "cross_analysis", "text": "생닭 급등이 마진을 압박.",
             "source_ref": ["생닭"], "impact": "negative"},
        ],
        "bep_guidance": {"text": "손익분기 매출 48,501,789원 수준.", "source_ref": ["bep.bep_revenue"]},
        "tax_risk_alerts": [
            {"text": "현금 매출 7.0% 미매칭 — POS 수량 기반 마진 신뢰도 저하 가능.",
             "source_ref": ["unallocated_cash_sales"], "severity": "medium"},
        ],
        "adherence_review": [],
        "milestone_review": [],
        "out_of_scope": ["부채/현금흐름은 본 분석 범위 아님"],
    }


def _valid_bs_output():
    return {
        "agent": "bs_analyst",
        "summary": "유동비율 153.2%로 단기 지급능력 양호.",
        "findings": [
            {"topic": "owner_draws", "text": "가수금 12,000,000원 혼용 리스크.",
             "source_ref": ["owner_draws.suspense_receipts"], "severity": "high"},
            {"topic": "cash_conversion", "text": "DSCR 2.22 수준.",
             "source_ref": ["dscr"], "severity": "low"},
        ],
        "cash_reserve_alerts": [
            {"text": "현금 생존기간 2.7개월 — 세금 납부용 현금 유보 권고.",
             "source_ref": ["cash_flow.cash_runway_months"], "severity": "high"},
        ],
        "adherence_review": [],
        "out_of_scope": ["품목 마진/BEP는 본 분석 범위 아님"],
    }


# --- 정상 파이프라인 ---
def test_analyze_pl_valid_pipeline():
    out = analyze_pl(
        _financials_pl(),
        sample_client_profile(),
        _followup(),
        rag_context=None,
        client=_FakeClient(_valid_pl_output()),
    )
    assert out["agent"] == "pl_analyst"
    assert out["bep_guidance"]["text"]


def test_analyze_bs_valid_pipeline():
    out = analyze_bs(
        _financials_bs(),
        sample_client_profile(),
        _followup(),
        rag_context=None,
        client=_FakeClient(_valid_bs_output()),
    )
    assert out["agent"] == "bs_analyst"
    assert out["cash_reserve_alerts"][0]["severity"] == "high"


# --- 환각 수치 → NumericGuardViolation ---
def test_analyze_pl_hallucination_blocked():
    bad = _valid_pl_output()
    bad["findings"].append(
        {"topic": "item_margin", "text": "가짜 공헌이익 99,999,999원.",
         "source_ref": ["x"], "impact": "neutral"}
    )
    with pytest.raises(NumericGuardViolation):
        analyze_pl(
            _financials_pl(), sample_client_profile(), _followup(),
            client=_FakeClient(bad),
        )


def test_analyze_bs_hallucination_blocked():
    bad = _valid_bs_output()
    bad["findings"].append(
        {"topic": "leverage", "text": "가짜 부채비율 987.6%.",
         "source_ref": ["x"], "severity": "high"}
    )
    with pytest.raises(NumericGuardViolation):
        analyze_bs(
            _financials_bs(), sample_client_profile(), _followup(),
            client=_FakeClient(bad),
        )


# --- 스키마 위반 → ValueError ---
def test_analyze_pl_schema_violation_blocked():
    bad = _valid_pl_output()
    del bad["bep_guidance"]  # 필수 필드 누락
    with pytest.raises(ValueError):
        analyze_pl(
            _financials_pl(), sample_client_profile(), _followup(),
            client=_FakeClient(bad),
        )


def test_analyze_bs_baseline_mode_runs():
    """신규 고객(baseline) followup 도 에러 없이 통과."""
    out = analyze_bs(
        _financials_bs(),
        sample_client_profile(),
        {"is_first_round": True, "current_period": "2025-Q3"},
        client=_FakeClient(_valid_bs_output()),
    )
    assert out["agent"] == "bs_analyst"
