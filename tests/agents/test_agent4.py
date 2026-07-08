"""Agent 4(최종 발행가) v0.3 파이프라인 테스트 — API 모킹.

Fake client 로 복합 산출을 반환시켜: (1) expert_feedback(overall_note)가 applied_feedback 에
scope:global 로 반영되는지, (2) dashboard_payload(hero_kpis)·recommendations 가 각 standalone
스키마를 통과하는지, (3) 확정 수치만 인용해 numeric_guard 를 통과하는지, 환각은 차단되는지 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.final_publisher import analyze_final
from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from guards.numeric_guard import NumericGuardViolation

FIXED_TS = "2026-07-07T09:00:00+09:00"


class _FakeClient:
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
        "previous_recommendations": [
            {"rec_code": "R-2025Q2-01", "text": "매출채권 회수기일 단축",
             "target_metric": "ar_days", "direction": "decrease", "status": "in_progress"},
        ],
        "metric_progress": [
            {"code": "ar_days", "prev_value": 42.0, "value": 33.0, "direction": "improved"},
        ],
    }


def _expert_feedback():
    return {
        "reviewer": "세무 파트너",
        "overall_note": "전체적으로 사장님이 겁먹지 않게 톤을 부드럽게, 현금 유보를 최우선으로 강조.",
        "instructions": [
            {"target_section": "overview", "directive": "일일 목표 판매수량을 맨 앞에 배치", "priority": "high"},
        ],
    }


def _a3_draft():
    return {
        "agent": "report_master",
        "sections": [
            {"id": "overview", "title": "종합 요약", "body_md": "하루 35개면 손익분기. 현금 2.7개월."},
        ],
        "contradiction_flags": [],
        "cited_values": ["bep.daily_target_qty"],
    }


def _valid_a4_output():
    """확정 수치(BEP 268.0/35, OPM 14.2, runway 2.7, DSCR 2.22, CCC 22.7)만 인용."""
    return {
        "agent": "final_publisher",
        "version": 2,
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "하루 35개(후라이드)면 손익분기입니다. 현금 생존기간 2.7개월이니 세금 유보를 "
                        "가장 먼저 챙기세요. 영업이익률은 14.2%로 견조합니다."},
        ],
        "applied_feedback": [
            {"from": "세무 파트너", "directive_ref": "overall_note",
             "how_applied": "전체 톤을 부드럽게 조정하고 현금 유보를 최상단에 배치.", "scope": "global"},
            {"from": "세무 파트너", "directive_ref": "overview",
             "how_applied": "일일 목표 판매수량을 종합 요약 맨 앞에 배치.", "scope": "section"},
        ],
        "dashboard_payload": {
            "client": {"name": "○○치킨 역삼점", "period": "2025년 3분기"},
            "hero_kpis": [
                {"key": "bep_attainment", "label": "손익분기점 달성률", "value_pct": 268.0,
                 "target_ref": "bep.bep_revenue"},
                {"key": "daily_target_qty", "label": "일일 목표 판매수량(후라이드 기준)", "value": 35},
            ],
            "kpis": [
                {"key": "OPM", "label": "영업이익률", "value_pct": 14.2, "trend": "flat"},
                {"key": "cash_runway", "label": "현금 생존기간", "value": 2.7, "unit": "개월", "flag": "watch"},
                {"key": "dscr", "label": "상환능력(DSCR)", "value": 2.22},
                {"key": "ccc", "label": "현금전환주기", "value": 22.7, "unit": "일"},
            ],
            "followup": {
                "prev_period": "2025-Q2",
                "items": [
                    {"rec_code": "R-2025Q2-01", "label": "회수기일 단축", "status": "improved",
                     "metric_ref": "ar_days"},
                ],
            },
            "charts": [
                {"id": "op_trend", "type": "line", "series_ref": ["OP"], "periods": ["2025-Q2", "2025-Q3"]},
            ],
            "report_sections": [
                {"id": "overview", "title": "종합 요약", "body_md": "하루 35개면 손익분기입니다."},
            ],
        },
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "세금 납부용 현금 유보 우선 확보",
             "target_metric": "cash_runway_months", "direction": "increase", "status": "proposed"},
            {"rec_code": "R-2025Q3-02", "text": "저마진 품목(콜라) 구성 재검토로 공헌이익 개선",
             "target_metric": "contribution_margin", "direction": "increase", "status": "proposed"},
        ],
    }


def test_analyze_final_valid_pipeline():
    out = analyze_final(
        _a3_draft(), _expert_feedback(), _financials_pl(), _financials_bs(),
        sample_client_profile(), _followup(),
        client=_FakeClient(_valid_a4_output()),
    )
    assert out["agent"] == "final_publisher"
    # hero_kpis 존재 + BEP 두 지표 포함
    hero_keys = {k["key"] for k in out["dashboard_payload"]["hero_kpis"]}
    assert {"bep_attainment", "daily_target_qty"} <= hero_keys


def test_overall_note_applied_globally():
    """overall_note 가 applied_feedback 에 scope:global 로 추적된다."""
    out = analyze_final(
        _a3_draft(), _expert_feedback(), _financials_pl(), _financials_bs(),
        sample_client_profile(), _followup(),
        client=_FakeClient(_valid_a4_output()),
    )
    globals_ = [a for a in out["applied_feedback"] if a.get("scope") == "global"]
    assert globals_ and globals_[0]["directive_ref"] == "overall_note"


def test_recommendations_pass_standalone_schema():
    """복합 산출의 recommendations 가 recommendations.json 스키마를 통과(이중 검증)."""
    out = analyze_final(
        _a3_draft(), _expert_feedback(), _financials_pl(), _financials_bs(),
        sample_client_profile(), _followup(),
        client=_FakeClient(_valid_a4_output()),
    )
    assert len(out["recommendations"]) == 2
    assert out["recommendations"][0]["direction"] == "increase"


def test_analyze_final_hallucination_blocked():
    bad = _valid_a4_output()
    bad["sections"][0]["body_md"] += " 그리고 가짜 부채비율 987.6%."
    with pytest.raises(NumericGuardViolation):
        analyze_final(
            _a3_draft(), _expert_feedback(), _financials_pl(), _financials_bs(),
            sample_client_profile(), _followup(),
            client=_FakeClient(bad),
        )


def test_analyze_final_bad_dashboard_blocked():
    """대시보드 하위 계약 위반(hero_kpi 에 value/value_pct 둘 다 없음) → ValueError."""
    bad = _valid_a4_output()
    bad["dashboard_payload"]["hero_kpis"][1] = {"key": "daily_target_qty", "label": "일일 목표"}
    with pytest.raises(ValueError):
        analyze_final(
            _a3_draft(), _expert_feedback(), _financials_pl(), _financials_bs(),
            sample_client_profile(), _followup(),
            client=_FakeClient(bad),
        )
