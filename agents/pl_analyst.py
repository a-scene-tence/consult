"""Agent 1 — PL 분석가 (SPEC §2.1).

대형 건설사 관리회계 전문가 페르소나. `financials.pl` 확정 수치만 읽어 수익성·원가구조·
영업이익률 변동요인·마일스톤 성과를 분석하고 `agent1_pl_analysis` 스키마로 출력한다.
출력은 스키마 검증 + numeric_guard(환각 수치 방어)를 모두 통과해야 한다.
"""

from __future__ import annotations

from typing import Any

from agents.base import build_system_prompt, build_user_message, structured_call
from compute._common import validate_payload
from guards.numeric_guard import NumericGuardViolation, find_hallucinated_numbers

AGENT_NAME = "pl_analyst"
_TOOL_NAME = "emit_pl_analysis"
_SCHEMA_NAME = "agent1_pl_analysis"

_PERSONA = "대형 건설사에서 원가 관리·예산 교차검증·마일스톤 성과 모니터링을 수행해 온 관리회계 전문가"
_RESPONSIBILITIES = (
    "- 수익성 분석(매출총이익률·영업이익률 등)\n"
    "- 원가 구조 분석\n"
    "- 영업이익률 변동 요인\n"
    "- 마일스톤 기반 성과(계획 대비 실적)"
)
_BOUNDARIES = (
    "- BS(운전자본·부채·현금흐름) 리스크는 판단하지 않는다.\n"
    "- 세무 이슈, 최종 종합 결론은 다루지 않는다.\n"
    "- 위 주제는 out_of_scope에 '본 분석 범위 아님'으로 표기한다."
)


def analyze_pl(
    financials_pl: dict[str, Any],
    rag_context: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """PL 확정 수치로부터 agent1_pl_analysis 출력을 산출한다.

    Raises:
        NumericGuardViolation: 출력에 입력 Golden Set에 없는 재무 수치(환각)가 있을 때.
        jsonschema.ValidationError 계열: 출력이 스키마를 위반할 때(validate_payload).
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES)
    user = build_user_message(financials_pl, rag_context)
    output = structured_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="PL 분석 결과를 지정 스키마로 제출한다.",
        input_schema=_load_schema(),
        client=client,
    )

    validate_payload(output, _SCHEMA_NAME)

    violations = find_hallucinated_numbers(financials_pl, output)
    if violations:
        raise NumericGuardViolation(
            f"[{AGENT_NAME}] 환각 수치 감지(입력에 없는 값): {violations}"
        )
    return output


def _load_schema() -> dict[str, Any]:
    from compute._common import load_schema

    return load_schema(_SCHEMA_NAME)
