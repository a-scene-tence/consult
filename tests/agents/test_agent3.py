"""Agent 3(리포트 마스터) v0.3 파이프라인 테스트 — API 모킹.

Fake Anthropic client 로 mock 초안을 반환시켜, analyze_report 가 스키마 검증 + numeric_guard
([a1, a2, profile])를 통과하는지, 상충하는 A1·A2 입력에서 mock 이 contradiction_flags 를 채우고
통과하는지, 환각 수치는 차단되는지 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.report_master import analyze_report
from compute.ingest import sample_client_profile
from guards.numeric_guard import NumericGuardViolation


class _FakeClient:
    """messages.create 가 지정 output 을 tool_use 블록으로 반환하는 스텁."""

    def __init__(self, output: dict):
        self._output = output
        self.messages = self

    def create(self, **kwargs):
        tool_name = kwargs["tools"][0]["name"]
        block = SimpleNamespace(type="tool_use", name=tool_name, input=self._output)
        return SimpleNamespace(content=[block])


# --- 상충하는 A1·A2 입력(골든 소스) ---
def _a1_marketing_invest():
    """Agent 1: 여력이 있으니 마케팅 투자 확대 권고(하루 35개 목표)."""
    return {
        "agent": "pl_analyst",
        "summary": "영업이익률 14.2%로 견조 — 공격적 마케팅 여지 있음.",
        "findings": [
            {"topic": "op_margin", "text": "영업이익률 14.2%로 개선세, 배달 마케팅 투자 확대 권고.",
             "source_ref": ["OPM"], "impact": "positive"},
        ],
        "bep_guidance": {"text": "하루 35개(후라이드 기준)면 손익분기.", "source_ref": ["bep.daily_target_qty"]},
        "tax_risk_alerts": [],
        "adherence_review": [],
        "milestone_review": [],
        "out_of_scope": ["부채/현금흐름은 본 분석 범위 아님"],
    }


def _a2_cash_depletion():
    """Agent 2: 현금 2.7개월 — 세금 유보·투자 자제 경고."""
    return {
        "agent": "bs_analyst",
        "summary": "현금 생존기간 2.7개월로 취약 — 지출 확대 경계.",
        "findings": [
            {"topic": "cash_conversion", "text": "DSCR 2.22로 상환 압박 존재.",
             "source_ref": ["dscr"], "severity": "medium"},
        ],
        "cash_reserve_alerts": [
            {"text": "현금 생존기간 2.7개월 — 세금 납부용 유보 필요, 신규 투자 자제 권고.",
             "source_ref": ["cash_flow.cash_runway_months"], "severity": "high"},
        ],
        "adherence_review": [],
        "out_of_scope": ["품목 마진/BEP는 본 분석 범위 아님"],
    }


def _draft_with_contradiction():
    return {
        "agent": "report_master",
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "핵심: 하루 35개를 팔면 손익분기입니다. 다만 현금 생존기간이 2.7개월로 짧아 "
                        "세금 납부용 현금을 먼저 확보하세요."},
            {"id": "balance", "title": "수익성 vs 현금",
             "body_md": "영업이익률 14.2%는 좋지만, 현금 여력(2.7개월)을 감안해 투자 시점을 조정해야 합니다."},
        ],
        "contradiction_flags": [
            {"between": ["pl_analyst", "bs_analyst"],
             "text": "PL은 마케팅 투자 확대를 권고하나 BS는 현금 2.7개월 경고로 지출 자제를 권고 — 상충.",
             "resolved": True},
        ],
        "cited_values": ["bep.daily_target_qty", "cash_flow.cash_runway_months", "OPM"],
    }


def _draft_no_contradiction():
    return {
        "agent": "report_master",
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "하루 35개면 손익분기. 현금 생존기간 2.7개월은 유의하되 전반적으로 안정적입니다."},
        ],
        "contradiction_flags": [],
        "cited_values": ["bep.daily_target_qty"],
    }


def test_analyze_report_flags_contradiction():
    out = analyze_report(
        _a1_marketing_invest(), _a2_cash_depletion(), sample_client_profile(),
        client=_FakeClient(_draft_with_contradiction()),
    )
    assert out["agent"] == "report_master"
    assert len(out["contradiction_flags"]) >= 1
    flag = out["contradiction_flags"][0]
    assert set(flag["between"]) == {"pl_analyst", "bs_analyst"}


def test_analyze_report_no_contradiction_passes():
    out = analyze_report(
        _a1_marketing_invest(), _a2_cash_depletion(), sample_client_profile(),
        client=_FakeClient(_draft_no_contradiction()),
    )
    assert out["contradiction_flags"] == []


def test_analyze_report_hallucination_blocked():
    bad = _draft_with_contradiction()
    bad["sections"].append(
        {"id": "fake", "title": "가짜", "body_md": "가짜 공헌이익 88,888,888원이 발생."}
    )
    with pytest.raises(NumericGuardViolation):
        analyze_report(
            _a1_marketing_invest(), _a2_cash_depletion(), sample_client_profile(),
            client=_FakeClient(bad),
        )


def test_analyze_report_schema_violation_blocked():
    bad = _draft_no_contradiction()
    del bad["cited_values"]  # 필수 필드 누락
    with pytest.raises(ValueError):
        analyze_report(
            _a1_marketing_invest(), _a2_cash_depletion(), sample_client_profile(),
            client=_FakeClient(bad),
        )
