"""Agent 3 — 리포트 마스터 (SPEC §2.3, v0.3).

두 전문가(Agent 1·2)의 분석을 고객 언어로 통역·종합하는 컨설팅 에디터 페르소나.
`agent1_pl_analysis` + `agent2_bs_analysis` (+ 선택 `client_profile`)를 입력받아 1차 초안
(sections)과 **상호 모순 점검**(contradiction_flags)을 `agent3_draft_report` 스키마로 출력한다.

새 재무 판단·수치 재계산은 하지 않는다(무연산). 초안이 인용하는 수치는 Agent 1·2가 이미
확정 JSON에서 인용해 검증을 통과한 값뿐이므로, numeric_guard의 Golden Set은
[agent1_output, agent2_output, client_profile]로 구성한다.
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import _section, build_system_prompt, structured_call
from compute._common import load_schema, validate_payload
from guards.numeric_guard import (
    NumericGuardViolation,
    find_hallucinated_numbers,
)

AGENT_NAME = "report_master"
_TOOL_NAME = "emit_draft_report"
_SCHEMA_NAME = "agent3_draft_report"

_PERSONA = "두 전문가(PL 분석가·BS 분석가)의 분석을 사장님 언어로 통역·종합하는 컨설팅 에디터"
_RESPONSIBILITIES = (
    "- Agent 1(수익성·품목 마진·BEP)과 Agent 2(현금흐름·부채·세무 Cash Reserve) 결과를 통합해\n"
    "  하나의 일관된 초안(sections)으로 재구성\n"
    "- 두 분석 간 상호 모순 점검(contradiction_flags)\n"
    "- client_profile 눈높이에 맞춘 톤 유지, 전후 비교(이행 성과) 요약 반영"
)
_BOUNDARIES = (
    "- 새로운 재무 판단을 생성하지 않는다(두 분석의 종합·통역만).\n"
    "- 수치를 재계산·추정하지 않는다 — Agent 1·2가 인용한 확정 수치만 사용한다.\n"
    "- 역할 경계 밖 판단은 하지 않는다."
)
# 사용자 요구 3대 프롬프트 로직(SPEC §2.3 프롬프트 전략).
_TASK_RULES = (
    "[초안 작성 지침]\n"
    "1. Agent 1·2의 전문 용어를 사장님이 바로 이해할 수 있는 쉬운 언어로 통역·종합하라.\n"
    "2. **종합 요약(sections 첫 섹션) 최상단에 (a) BEP 일일 판매 목표(Agent 1 bep_guidance)와\n"
    "   (b) 세금 납부용 Cash Reserve 권고(Agent 2 cash_reserve_alerts)를 가장 중요한 핵심\n"
    "   메시지로 배치**하라.\n"
    "3. Agent 1과 Agent 2의 권고가 상충하는지 반드시 점검하라. 예: Agent 1이 마케팅/설비 투자\n"
    "   확대를 권고하는데 Agent 2가 현금 고갈(짧은 Cash Runway)·세금 유보를 경고하면, 이를\n"
    "   contradiction_flags 에 명시하고(between: [pl_analyst, bs_analyst]), 초안 본문에서는\n"
    "   우선순위(현금 안전 우선 등)나 선행 조건을 달아 조정하라(resolved 로 표시).\n"
    "4. 인용한 확정 수치 근거는 cited_values 에 코드/품목으로 남겨라(재계산 금지)."
)


def _build_report_user_message(
    agent1_output: dict[str, Any],
    agent2_output: dict[str, Any],
    client_profile: dict[str, Any] | None,
) -> str:
    """Agent 3 사용자 메시지 — 두 분석 결과(+프로필)를 라벨 섹션으로 주입."""
    parts = [
        "[Agent 1 — PL 분석 결과] — 이 안의 수치만 인용하라(재계산 금지):",
        json.dumps(agent1_output, ensure_ascii=False, indent=2),
        "",
        "[Agent 2 — BS 분석 결과] — 이 안의 수치만 인용하라(재계산 금지):",
        json.dumps(agent2_output, ensure_ascii=False, indent=2),
    ]
    if client_profile:
        parts += _section("[고객 프로필] — 톤·눈높이 조정용(수치 변경 금지):", client_profile)
    parts += [
        "",
        "위 두 분석을 종합해 1차 초안을 작성하고, 상호 모순을 점검하라. 반드시 제공된 도구를 호출해"
        " 지정 JSON 스키마로만 응답하라.",
    ]
    return "\n".join(parts)


def analyze_report(
    agent1_output: dict[str, Any],
    agent2_output: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Agent 1·2 출력으로부터 agent3_draft_report 초안을 산출한다.

    Raises:
        NumericGuardViolation: 초안에 Agent 1·2/프로필에 없는 재무 수치(환각)가 있을 때.
        ValueError: 출력이 스키마를 위반할 때(validate_payload).
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES) + "\n\n" + _TASK_RULES
    user = _build_report_user_message(agent1_output, agent2_output, client_profile)
    output = structured_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="종합 초안과 상호 모순 점검 결과를 지정 스키마로 제출한다.",
        input_schema=load_schema(_SCHEMA_NAME),
        client=client,
    )

    validate_payload(output, _SCHEMA_NAME)

    sources = [src for src in (agent1_output, agent2_output, client_profile) if src is not None]
    violations = find_hallucinated_numbers(sources, output)
    if violations:
        raise NumericGuardViolation(
            f"[{AGENT_NAME}] 환각 수치 감지(Golden Set에 없는 값): {violations}"
        )
    return output
