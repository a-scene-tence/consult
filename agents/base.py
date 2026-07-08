"""에이전트 공통 베이스 (SPEC §2.5, CLAUDE.md §2.3).

- 모든 에이전트 시스템 프롬프트에 삽입되는 **공통 불변 규약** 템플릿.
- Anthropic Messages API를 **forced tool_choice**로 호출해 지정 JSON 스키마로만 응답하도록 강제.
- RAG 조회를 대비해 `rag_context` 매개변수를 받되, 현재는 빈 값(None)으로 넘길 수 있다.

에이전트 레이어는 **무연산(Strict Rule)**이다. 숫자는 확정 JSON에서 인용만 하며,
출력의 수치 환각은 `guards.numeric_guard`가 최종 방어한다.
"""

from __future__ import annotations

import json
import os
from typing import Any

# 기본 모델 문자열 (CLAUDE.md §1). .env 의 LLM_MODEL 로 재정의 가능.
DEFAULT_MODEL = "claude-opus-4-8"

# SPEC §2.5 — 모든 에이전트 시스템 프롬프트에 공통 삽입되는 불변 규약(v0.3, 8개 항).
COMMON_RULES = """[불변 규약]
1. 너는 숫자를 계산·추정·반올림하지 않는다. 제공된 확정 수치(JSON)의 값만 그대로 인용한다.
   BEP·CCC·DSCR·마진 등 파생 지표도 시스템이 이미 계산했다 — 인용만 하라.
2. 제공된 데이터에 없는 수치를 언급하지 않는다. 없으면 "데이터 없음"이라고 적는다.
3. 역할 범위 밖 주제는 판단하지 않고 out_of_scope 에 "본 분석 범위 아님"으로 표기한다.
4. 모든 정량적 주장에는 source_ref(계정과목/품목/지표 코드)를 붙인다.
5. 출력은 반드시 제공된 도구(tool)를 호출하여 지정된 JSON 스키마로만 응답한다.
6. client_profile(성별·연령대·상권·성향)을 고려해 현실적으로 실행 가능한 눈높이로 서술하되,
   프로필을 이유로 수치를 바꾸지 않는다.
7. followup_context 의 직전 권고·이행 지표는 시스템이 확정한 값이다. 이행 성과를 서술할 때도
   metric_progress 의 값만 인용한다.
8. rag_context(과거 코호트 케이스)는 정성 참고 전용이다. 과거 수치를 현재 고객 확정치로
   인용하지 않는다."""


class AgentOutputError(Exception):
    """모델 응답에서 구조화 출력(tool_use)을 얻지 못했을 때 발생."""


def _default_client() -> Any:
    """실사용 시 Anthropic 클라이언트를 생성(테스트는 client를 주입)."""
    import anthropic  # 지연 임포트: 테스트에서 모킹 시 불필요한 초기화 방지

    return anthropic.Anthropic()


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def build_system_prompt(persona: str, responsibilities: str, boundaries: str) -> str:
    """페르소나 + 책임 범위 + 역할 경계 + 공통 불변 규약으로 시스템 프롬프트 구성."""
    return (
        f"너는 {persona}이다.\n\n"
        f"[책임 범위]\n{responsibilities}\n\n"
        f"[역할 경계 — 판단하지 않는 것]\n{boundaries}\n\n"
        f"{COMMON_RULES}"
    )


def _section(label: str, obj: Any) -> list[str]:
    return ["", label, json.dumps(obj, ensure_ascii=False, indent=2)]


def build_user_message(
    financials: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    followup_context: dict[str, Any] | None = None,
    rag_context: dict[str, Any] | None = None,
) -> str:
    """4종 컨텍스트(확정 수치 + 프로필 + Follow-up + RAG)를 담은 사용자 메시지 구성.

    존재하는 컨텍스트만 라벨 섹션으로 주입한다(baseline 이면 followup 생략).
    """
    parts = [
        "[확정 재무 수치] — 이 값만 인용하라(파생 지표 포함, 재계산 금지):",
        json.dumps(financials, ensure_ascii=False, indent=2),
    ]
    if client_profile:
        parts += _section(
            "[고객 프로필] — 톤·솔루션 눈높이 조정용(수치 변경 금지):", client_profile
        )
    if followup_context and not followup_context.get("is_first_round", False):
        parts += _section(
            "[Follow-up] — 직전 권고와 이행 지표(metric_progress). 이행 성과를 이 값으로 서술:",
            followup_context,
        )
    elif followup_context and followup_context.get("is_first_round", False):
        parts += ["", "[Follow-up] 신규 고객(baseline). 직전 이력 없음 — 이행 점검 대신 기준선 관점으로 서술."]
    if rag_context and rag_context.get("cases"):
        parts += _section(
            "[참고: 유사 과거 코호트 케이스 — 정성 참고 전용, 수치 인용 금지]", rag_context
        )
    parts += [
        "",
        "위 컨텍스트를 바탕으로 분석하고, 반드시 제공된 도구를 호출해 지정 JSON 스키마로만 응답하라.",
    ]
    return "\n".join(parts)


def _tool_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema 문서에서 tool input_schema로 부적합한 메타 키를 제거."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")}


def _default_max_attempts() -> int:
    """자가치유 재시도 최대 횟수(.env LLM_MAX_ATTEMPTS, 기본 3)."""
    try:
        return max(1, int(os.environ.get("LLM_MAX_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _structured_call_messages(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    client: Any | None,
    max_tokens: int,
) -> dict[str, Any]:
    """messages 히스토리로 forced tool_choice 호출 → tool_use 입력(dict) 반환."""
    client = client or _default_client()
    response = client.messages.create(
        model=_resolve_model(),
        max_tokens=max_tokens,
        system=system,
        tools=[
            {
                "name": tool_name,
                "description": tool_description,
                "input_schema": _tool_input_schema(input_schema),
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
        messages=messages,
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise AgentOutputError("모델 응답에 tool_use 블록이 없습니다(구조화 출력 실패).")


def structured_call(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    client: Any | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """forced tool_choice로 Messages API를 1회 호출하고 tool_use 입력(dict)을 반환한다.

    `client`를 주입하면(테스트) 실제 네트워크 호출 없이 동작한다. 강제 도구 호출과의
    호환을 위해 thinking 파라미터는 사용하지 않는다.
    """
    return _structured_call_messages(
        system=system,
        messages=[{"role": "user", "content": user}],
        tool_name=tool_name,
        tool_description=tool_description,
        input_schema=input_schema,
        client=client,
        max_tokens=max_tokens,
    )


def _correction_message(exc: Exception) -> str:
    """실패 원인을 모델에 되먹이는 자가치유 교정 프롬프트(Strict Rule 위반 교정 유도)."""
    return (
        "[System Error] 직전 응답이 시스템 검증을 통과하지 못했습니다.\n"
        f"원인: {exc}\n"
        "너는 숫자를 계산·추정·반올림하지 않는다(Strict Rule). 입력으로 제공된 확정 수치(JSON)에 "
        "없는 숫자(예: '약 15%' 같은 근사치·환각)를 만들어냈다면 그 숫자를 빼거나, 반드시 확정 "
        "수치만 그대로 인용해서 다시 작성하라. 스키마 필수 필드가 누락됐다면 채워라. "
        "반드시 제공된 도구를 다시 호출해 지정 JSON 스키마로만 응답하라."
    )


def self_healing_call(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict[str, Any],
    validate: Any,
    client: Any | None = None,
    max_attempts: int | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """자가치유 구조화 호출 — 검증 실패 시 실패 원인을 주입해 최대 N회 재작성 유도.

    `validate(output)` 는 검증에 성공하면 조용히 반환하고, 실패하면 예외를 던진다(스키마
    `ValueError`·`NumericGuardViolation` 등). 마지막 시도까지 실패하면 그 예외를 그대로 전파해
    호출측 예외 타입 계약을 보존한다. 재시도 시 직전(불량) 출력과 교정 지시를 메시지 히스토리에
    추가 주입하여 모델이 스스로 오류를 교정하도록 유도한다(Self-Healing).
    """
    attempts = max_attempts or _default_max_attempts()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    for attempt in range(1, attempts + 1):
        output = _structured_call_messages(
            system=system,
            messages=messages,
            tool_name=tool_name,
            tool_description=tool_description,
            input_schema=input_schema,
            client=client,
            max_tokens=max_tokens,
        )
        try:
            validate(output)
            return output
        except Exception as exc:  # 스키마/가드 위반 등 — 마지막 시도면 전파
            if attempt >= attempts:
                raise
            # 직전 불량 출력(assistant)과 교정 지시(user)를 히스토리에 추가 → 자가 교정.
            messages.append(
                {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}
            )
            messages.append({"role": "user", "content": _correction_message(exc)})
    raise AgentOutputError("자가치유 재시도 로직 오류(도달 불가)")  # pragma: no cover
