"""자가치유(Self-Healing) 재시도 테스트 (Phase 7).

첫 호출에서 환각 수치(가짜 숫자)를 낸 뒤, 재시도에서 교정된 정상 출력을 내면 파이프라인이
성공하는지, 그리고 재시도 시 실패 원인이 User 메시지로 주입되는지 검증한다. 끝까지 실패하면
기존 예외 타입(NumericGuardViolation)이 보존되는지도 확인한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.pl_analyst import analyze_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_client_profile, sample_pl_raw
from guards.numeric_guard import NumericGuardViolation

FIXED_TS = "2026-07-07T09:00:00+09:00"


class _SequenceClient:
    """messages.create 호출마다 큐에서 다음 output 을 반환하고, 받은 messages 를 기록."""

    def __init__(self, outputs: list[dict]):
        self._outputs = list(outputs)
        self.calls: list[list] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs["messages"])
        out = self._outputs.pop(0)
        tool_name = kwargs["tools"][0]["name"]
        block = SimpleNamespace(type="tool_use", name=tool_name, input=out)
        return SimpleNamespace(content=[block])


def _financials_pl():
    return compute_pl(sample_pl_raw(), computed_at=FIXED_TS)


def _good_output():
    return {
        "agent": "pl_analyst",
        "summary": "영업이익률 14.2%로 개선.",
        "findings": [],
        "bep_guidance": {"text": "하루 35개면 손익분기.", "source_ref": ["bep.daily_target_qty"]},
        "tax_risk_alerts": [],
        "adherence_review": [],
        "milestone_review": [],
        "out_of_scope": ["부채/현금흐름은 본 분석 범위 아님"],
    }


def _hallucinated_output():
    bad = _good_output()
    bad["findings"] = [
        {"topic": "item_margin", "text": "가짜 공헌이익 99,999,999원.",
         "source_ref": ["x"], "impact": "neutral"}
    ]
    return bad


def test_self_heals_after_hallucination():
    """1차 환각 → 2차 교정 정상 → 성공 반환. 교정 메시지가 히스토리에 주입됨."""
    client = _SequenceClient([_hallucinated_output(), _good_output()])
    out = analyze_pl(
        _financials_pl(), sample_client_profile(),
        {"is_first_round": True, "current_period": "2025-Q3"},
        client=client,
    )
    assert out["bep_guidance"]["text"]
    # 2회 호출됐고, 2번째 호출의 메시지에 교정(System Error) 프롬프트가 포함
    assert len(client.calls) == 2
    second_msgs = client.calls[1]
    assert any("System Error" in m["content"] for m in second_msgs if m["role"] == "user")
    assert any(m["role"] == "assistant" for m in second_msgs)  # 직전 불량 출력 echo


def test_persistent_hallucination_raises(monkeypatch):
    """끝까지 환각이면 max_attempts 후 NumericGuardViolation 전파(예외 타입 보존)."""
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "3")
    client = _SequenceClient([_hallucinated_output()] * 3)
    with pytest.raises(NumericGuardViolation):
        analyze_pl(
            _financials_pl(), sample_client_profile(),
            {"is_first_round": True, "current_period": "2025-Q3"},
            client=client,
        )
    assert len(client.calls) == 3  # 3회 시도 후 포기
