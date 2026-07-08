"""Agent 2 — BS 분석가 (SPEC §2.2, v0.3).

세무/회계 전문 파트너 페르소나. `financials.bs` 확정 수치 + 고객 프로필 + Follow-up +
(선택) RAG 를 입력받아 실전 현금흐름(CCC·DSCR·Runway)·가수금 리스크·세무 캘린더 기반
Cash Reserve 를 분석하고 `agent2_bs_analysis` 스키마로 출력한다. 출력은 스키마 검증 +
numeric_guard(확장 Golden Set)를 통과해야 한다.
"""

from __future__ import annotations

from typing import Any

from agents.base import build_system_prompt, build_user_message, structured_call
from compute._common import load_schema, validate_payload
from guards.numeric_guard import (
    NumericGuardViolation,
    find_hallucinated_numbers,
    golden_sources,
)

AGENT_NAME = "bs_analyst"
_TOOL_NAME = "emit_bs_analysis"
_SCHEMA_NAME = "agent2_bs_analysis"

_PERSONA = "세무/회계 전문 파트너"
_RESPONSIBILITIES = (
    "- 실전 현금흐름 — CCC(현금전환주기)·DSCR(상환능력)·Cash Runway(생존개월)\n"
    "- 운전자본(AR/AP 일수), 부채 구조(대출별 금리·월 상환)\n"
    "- 가수금/가지급금(오너 자금 혼용) 리스크\n"
    "- 세무 캘린더 기반 Cash Reserve(현금 유보) 권고 — 흑자도산 방지\n"
    "- 직전 회차 BS 권고의 이행 성과(adherence_review)"
)
_BOUNDARIES = (
    "- PL 수익성/품목 마진/원가 구조는 판단하지 않는다.\n"
    "- BEP, 최종 종합 결론은 다루지 않는다.\n"
    "- 위 주제는 out_of_scope 에 '본 분석 범위 아님'으로 표기한다."
)
# 사용자 요구 3대 프롬프트 로직(SPEC §2.2 프롬프트 전략).
_TASK_RULES = (
    "[분석 지침]\n"
    "1. owner_draws 의 suspense_receipts(가수금)/suspense_payments(가지급금)가 0이 아니면\n"
    "   findings(topic: owner_draws, severity)에 오너 자금 혼용 리스크를 **반드시** 경고하라.\n"
    "2. upcoming_tax_events 에 1~2개월 내(months_until ≤ 2) 대형 세무 일정(부가세 등)이 있으면,\n"
    "   확정 산출된 cash_runway_months 와 가용현금(CASH)을 인용해 **세금 납부용 Cash Reserve(현금 유보)**를\n"
    "   cash_reserve_alerts 에 **최우선·강력히** 권고하라(흑자도산 방지, severity: high).\n"
    "3. cash_flow 의 CCC 와 DSCR 을 활용해 실질적 현금 회전과 대출 상환 압박을 findings 에 분석하라.\n"
    "4. client_profile 의 성향·연령대에 맞춰 부채·현금 관리 솔루션의 톤을 조정하라."
)


def analyze_bs(
    financials_bs: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    followup_context: dict[str, Any] | None = None,
    rag_context: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """BS 확정 수치로부터 agent2_bs_analysis 출력을 산출한다.

    Raises:
        NumericGuardViolation: 출력에 확장 Golden Set에 없는 재무 수치(환각)가 있을 때.
        ValueError: 출력이 스키마를 위반할 때(validate_payload).
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES) + "\n\n" + _TASK_RULES
    user = build_user_message(financials_bs, client_profile, followup_context, rag_context)
    output = structured_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="BS 분석 결과를 지정 스키마로 제출한다.",
        input_schema=load_schema(_SCHEMA_NAME),
        client=client,
    )

    validate_payload(output, _SCHEMA_NAME)

    sources = golden_sources(financials_bs, client_profile, followup_context)
    violations = find_hallucinated_numbers(sources, output)
    if violations:
        raise NumericGuardViolation(
            f"[{AGENT_NAME}] 환각 수치 감지(Golden Set에 없는 값): {violations}"
        )
    return output
