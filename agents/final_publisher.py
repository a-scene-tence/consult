"""Agent 4 — 최종 발행가 (SPEC §2.4, v0.3).

전문가 피드백을 **최우선(P3)**으로 반영해 최종본을 책임 발행하는 에디터 페르소나.
초안(`agent3_draft_report`) + `expert_feedback` + 원본 확정 수치(PL/BS) + 프로필/Follow-up 을
입력받아, 복합 산출(`agent4_final_report`)로 (1) 재작성 sections, (2) 피드백 반영 추적
(applied_feedback), (3) 고객 대시보드(`dashboard_payload`), (4) 다음 회차 Follow-up 기준
`recommendations[]` 를 한 번에 반환한다.

복합 출력은 agent4 스키마로 검증한 뒤, `dashboard_payload`·`recommendations` 를 각 standalone
스키마로 **이중 검증**한다. 대시보드 수치의 원천은 확정 JSON 이므로 numeric_guard 의 Golden Set 은
[financials_pl, financials_bs, followup_context, client_profile] 로 구성한다.
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import _section, build_system_prompt, self_healing_call
from compute._common import load_schema, validate_payload
from guards.numeric_guard import (
    NumericGuardViolation,
    find_hallucinated_numbers,
)

AGENT_NAME = "final_publisher"
_TOOL_NAME = "emit_final_report"
_SCHEMA_NAME = "agent4_final_report"

_PERSONA = "전문가 피드백을 반영해 최종본을 책임 발행하는 컨설팅 에디터"
_RESPONSIBILITIES = (
    "- expert_feedback 최우선 반영 재작성(applied_feedback 로 반영 내역 추적)\n"
    "- 고객 대시보드(dashboard_payload) 구성 — 최우선 KPI는 BEP 달성률·일일 타겟 판매수량\n"
    "- 다음 회차 Follow-up 기준 recommendations[] 구조화 추출(rec_code·target_metric·direction)"
)
_BOUNDARIES = (
    "- 피드백과 배치되는 초안 고집 금지 — 인간 지시를 최우선한다.\n"
    "- 수치를 변경·재계산하지 않는다(확정 JSON 값 인용만)."
)
# 사용자 요구 3대 프롬프트 로직(SPEC §2.4 프롬프트 전략).
_TASK_RULES = (
    "[발행 지침]\n"
    "1. expert_feedback 를 **절대 최우선**으로 반영해 초안을 재작성하라. 특히 전역 지시\n"
    "   overall_note 는 리포트 전체에 적용하고, applied_feedback 에 반영 내역을 남겨라\n"
    "   (overall_note 반영 항목은 scope: \"global\", directive_ref: \"overall_note\").\n"
    "   개별 instructions 는 해당 섹션에 반영하고 scope: \"section\" 으로 추적하라.\n"
    "2. dashboard_payload 를 정확한 스키마로 구성하라. hero_kpis 에는 (a) bep_attainment\n"
    "   (financials_pl.bep.bep_attainment_pct)와 (b) daily_target_qty\n"
    "   (financials_pl.bep.daily_target_qty)를 **반드시** 포함하라. 모든 수치는 확정 JSON 에서\n"
    "   복사하며 재계산하지 않는다.\n"
    "3. 다음 회차 이행 추적을 위해 recommendations[] 를 구조화 추출하라 — 각 항목에 rec_code,\n"
    "   실행 지침 text, 추적 대상 target_metric(예: ar_days, contribution_margin), 목표\n"
    "   direction(increase/decrease/maintain)을 명시하라."
)


def _build_final_user_message(
    agent3_draft: dict[str, Any],
    expert_feedback: dict[str, Any] | None,
    financials_pl: dict[str, Any],
    financials_bs: dict[str, Any],
    client_profile: dict[str, Any] | None,
    followup_context: dict[str, Any] | None,
) -> str:
    """Agent 4 사용자 메시지 — 초안·피드백·확정 수치·프로필/Follow-up 주입."""
    parts = [
        "[1차 초안 — Agent 3] 재작성 대상:",
        json.dumps(agent3_draft, ensure_ascii=False, indent=2),
    ]
    if expert_feedback:
        parts += _section(
            "[전문가 피드백] — **절대 최우선** 반영(특히 overall_note 는 전역 적용):",
            expert_feedback,
        )
    else:
        parts += ["", "[전문가 피드백] 없음 — 초안을 그대로 다듬어 발행(applied_feedback 빈 배열)."]
    parts += _section("[확정 재무 수치 — PL] 대시보드/인용 원천(재계산 금지):", financials_pl)
    parts += _section("[확정 재무 수치 — BS] 대시보드/인용 원천(재계산 금지):", financials_bs)
    if client_profile:
        parts += _section("[고객 프로필] — 톤·대시보드 client 정보:", client_profile)
    if followup_context and not followup_context.get("is_first_round", False):
        parts += _section(
            "[Follow-up] — 대시보드 followup·이행 지표(metric_progress) 원천:", followup_context
        )
    parts += [
        "",
        "피드백을 최우선 반영해 최종본을 재작성하고, dashboard_payload 와 recommendations 를 구성하라."
        " 반드시 제공된 도구를 호출해 지정 JSON 스키마로만 응답하라.",
    ]
    return "\n".join(parts)


def analyze_final(
    agent3_draft: dict[str, Any],
    expert_feedback: dict[str, Any] | None,
    financials_pl: dict[str, Any],
    financials_bs: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    followup_context: dict[str, Any] | None = None,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """초안·피드백·확정 수치로부터 agent4_final_report 복합 산출을 낸다.

    복합 스키마 검증 후 dashboard_payload·recommendations 를 standalone 스키마로 이중 검증하고,
    numeric_guard(확장 Golden Set)를 통과시킨다.

    Raises:
        NumericGuardViolation: 산출에 확정 수치에 없는 재무 수치(환각)가 있을 때.
        ValueError: 복합/대시보드/권고 스키마를 위반할 때.
    """
    system = build_system_prompt(_PERSONA, _RESPONSIBILITIES, _BOUNDARIES) + "\n\n" + _TASK_RULES
    user = _build_final_user_message(
        agent3_draft, expert_feedback, financials_pl, financials_bs,
        client_profile, followup_context,
    )
    sources = [
        src
        for src in (financials_pl, financials_bs, followup_context, client_profile)
        if src is not None
    ]

    def _validate(output: dict[str, Any]) -> None:
        # 1) 복합 스키마 검증 → 2) 하위 계약 이중 검증(각 standalone 스키마) → 3) 환각 가드.
        validate_payload(output, _SCHEMA_NAME)
        validate_payload(output["dashboard_payload"], "dashboard_payload")
        validate_payload(output["recommendations"], "recommendations")
        violations = find_hallucinated_numbers(sources, output)
        if violations:
            raise NumericGuardViolation(
                f"[{AGENT_NAME}] 환각 수치 감지(Golden Set에 없는 값): {violations}"
            )

    return self_healing_call(
        system=system,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="최종본·대시보드·권고를 복합 스키마로 제출한다.",
        input_schema=load_schema(_SCHEMA_NAME),
        validate=_validate,
        client=client,
    )
