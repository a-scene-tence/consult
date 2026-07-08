"""Agent 2 — BS 분석가 (SPEC §2.2).

세무/회계 전문 파트너 페르소나. `financials.bs` 확정 수치만 읽어 운전자본·단기 현금흐름·
부채비율 등 재무 건전성/리스크를 분석하고 `agent2_bs_analysis` 스키마로 출력한다.
출력은 스키마 검증 + numeric_guard(환각 수치 방어)를 모두 통과해야 한다.
"""

from __future__ import annotations

from typing import Any

from agents.base import build_system_prompt, build_user_message, structured_call
from compute._common import validate_payload
from guards.numeric_guard import NumericGuardViolation, find_hallucinated_numbers

AGENT_NAME = "bs_analyst"
_TOOL_NAME = "emit_bs_analysis"
_SCHEMA_NAME = "agent2_bs_analysis"

_PERSONA = "세무/회계 전문 파트너"
_RESPONSIBILITIES = (
    "- 운전자본(Working Capital) 분석\n"
    "- 단기 현금흐름\n"
    "- 부채 비율 및 재무 건전성\n"
    "- 리스크 요소(항목별 severity 라벨링)"
)
_BOUNDARIES = (
    "- PL 수익성/원가 구조는 판단하지 않는다.\n"
    "- 마일스톤 성과, 최종 종합 결론은 다루지 않는다.\n"
    "- 위 주제는 out_of_scope에 '본 분석 범위 아님'으로 표기한다."
)


def analyze_bs(
    financials_bs: dict[str, Any],
    rag_context: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """BS 확정 수치로부터 agent2_bs_analysis 출력을 산출한다.

    Raises:
        NumericGuardViolation: 출력에 입력 Golden Set에 없는 재무 수치(환각)가 있을 때.
        jsonschema.ValidationError 계열: 출력이 스키마를 위반할 때(validate_payload).
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES)
    user = build_user_message(financials_bs, rag_context)
    output = structured_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="BS 분석 결과를 지정 스키마로 제출한다.",
        input_schema=_load_schema(),
        client=client,
    )

    validate_payload(output, _SCHEMA_NAME)

    violations = find_hallucinated_numbers(financials_bs, output)
    if violations:
        raise NumericGuardViolation(
            f"[{AGENT_NAME}] 환각 수치 감지(입력에 없는 값): {violations}"
        )
    return output


def _load_schema() -> dict[str, Any]:
    from compute._common import load_schema

    return load_schema(_SCHEMA_NAME)
