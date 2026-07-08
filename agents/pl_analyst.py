"""Agent 1 — PL 분석가 (SPEC §2.1, v0.3).

대형 건설사 관리회계 전문가 페르소나. `financials.pl` 확정 수치 + 고객 프로필 + Follow-up +
(선택) RAG 를 입력받아 미시 마진·BEP·세무 리스크·교차 분석을 수행하고 `agent1_pl_analysis`
스키마로 출력한다. 출력은 스키마 검증 + numeric_guard(확장 Golden Set)를 통과해야 한다.
"""

from __future__ import annotations

from typing import Any

from agents.base import build_system_prompt, build_user_message, self_healing_call
from compute._common import load_schema, validate_payload
from guards.numeric_guard import (
    NumericGuardViolation,
    find_hallucinated_numbers,
    golden_sources,
)

AGENT_NAME = "pl_analyst"
_TOOL_NAME = "emit_pl_analysis"
_SCHEMA_NAME = "agent1_pl_analysis"

_PERSONA = "대형 건설사에서 원가 관리·예산 교차검증·마일스톤 성과 모니터링을 수행해 온 관리회계 전문가"
_RESPONSIBILITIES = (
    "- 품목별 마진 구조(P*Q) — 효자/부진 품목(margin_rank), 공헌이익\n"
    "- 원가(COGS)·판관비(OPEX) 세부 변동\n"
    "- 손익분기점(BEP) 타겟팅 — 일일 판매 목표 직관 제시\n"
    "- 현금 매출 누락 기반 세무 리스크 경고\n"
    "- 직전 회차 PL 권고의 이행 성과(adherence_review)"
)
_BOUNDARIES = (
    "- BS(운전자본·부채·현금흐름·가수금) 리스크는 판단하지 않는다.\n"
    "- 세무 일정(Cash Reserve), 최종 종합 결론은 다루지 않는다.\n"
    "- 위 주제는 out_of_scope 에 '본 분석 범위 아님'으로 표기한다."
)
# 사용자 요구 4대 프롬프트 로직(SPEC §2.1 프롬프트 전략).
_TASK_RULES = (
    "[분석 지침]\n"
    "1. client_profile 의 성별·연령대·상권·risk_appetite 를 고려해 현실적으로 실행 가능한 톤으로 제안하라.\n"
    "2. bep.daily_target_qty 와 anchor_item 을 인용해 \"하루 ○○개(주력상품)를 팔면 손익분기\"라는\n"
    "   직관적 가이던스를 bep_guidance 에 **반드시** 포함하라(source_ref: bep.daily_target_qty).\n"
    "3. unallocated_cash_sales.risk_flag 가 'warn' 또는 'critical' 이면 tax_risk_alerts 에 세무 리스크를\n"
    "   경고하고, **\"현금 매출 누락으로 POS 수량(Q) 기반 품목별 마진 분석의 신뢰도가 떨어질 수 있음\"**을\n"
    "   반드시 지적하라.\n"
    "4. cogs_details 의 폭등 원자재(yoy_pct 상위)와 sales_details 의 마진 하락 품목을 교차 분석해\n"
    "   findings(topic: cross_analysis)에 서술하라."
)


def analyze_pl(
    financials_pl: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    followup_context: dict[str, Any] | None = None,
    rag_context: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """PL 확정 수치로부터 agent1_pl_analysis 출력을 산출한다.

    Raises:
        NumericGuardViolation: 출력에 확장 Golden Set에 없는 재무 수치(환각)가 있을 때.
        ValueError: 출력이 스키마를 위반할 때(validate_payload).
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES) + "\n\n" + _TASK_RULES
    user = build_user_message(financials_pl, client_profile, followup_context, rag_context)
    sources = golden_sources(financials_pl, client_profile, followup_context)

    def _validate(output: dict[str, Any]) -> None:
        validate_payload(output, _SCHEMA_NAME)  # 스키마 위반 → ValueError
        violations = find_hallucinated_numbers(sources, output)
        if violations:
            raise NumericGuardViolation(
                f"[{AGENT_NAME}] 환각 수치 감지(Golden Set에 없는 값): {violations}"
            )

    return self_healing_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="PL 분석 결과를 지정 스키마로 제출한다.",
        input_schema=load_schema(_SCHEMA_NAME),
        validate=_validate,
        client=client,
    )
