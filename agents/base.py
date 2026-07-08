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

# SPEC §2.5 — 모든 에이전트 시스템 프롬프트에 공통 삽입되는 불변 규약.
COMMON_RULES = """[불변 규약]
1. 너는 숫자를 계산·추정·반올림하지 않는다. 제공된 확정 수치(JSON)의 값만 그대로 인용한다.
2. 제공된 데이터에 없는 수치를 언급하지 않는다. 필요한 값이 없으면 "데이터 없음"이라고 적는다.
3. 너의 역할 범위를 벗어난 주제는 판단하지 않고 "본 분석 범위 아님"으로 표기한다.
4. 모든 정량적 주장에는 근거가 되는 source_ref(계정과목/기간 코드)를 붙인다.
5. 출력은 반드시 제공된 도구(tool)를 호출하여 지정된 JSON 스키마로만 응답한다.
6. 수치는 제공된 형식·값 그대로 인용한다(단위 재표현·근사·추가 반올림 금지).
7. rag_context(유사 과거 케이스)는 정성 참고 전용이다. 과거 케이스의 수치를 현재 고객의
   확정치로 인용하지 않는다."""


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


def build_user_message(
    financials: dict[str, Any], rag_context: dict[str, Any] | None = None
) -> str:
    """확정 수치(+선택 rag_context)를 담은 사용자 메시지 구성."""
    parts = [
        "다음은 시스템이 확정한 재무 수치(JSON)이다. 이 값만 인용하라:",
        json.dumps(financials, ensure_ascii=False, indent=2),
    ]
    if rag_context and rag_context.get("cases"):
        parts += [
            "",
            "[참고: 유사 과거 케이스 — 정성 참고 전용, 수치 인용 금지]",
            json.dumps(rag_context, ensure_ascii=False, indent=2),
        ]
    parts += [
        "",
        "위 확정 수치를 바탕으로 분석하고, 반드시 제공된 도구를 호출해 지정 JSON 스키마로만 응답하라.",
    ]
    return "\n".join(parts)


def _tool_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema 문서에서 tool input_schema로 부적합한 메타 키를 제거."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title")}


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
    """forced tool_choice로 Messages API를 호출하고 tool_use 입력(dict)을 반환한다.

    `client`를 주입하면(테스트) 실제 네트워크 호출 없이 동작한다. 강제 도구 호출과의
    호환을 위해 thinking 파라미터는 사용하지 않는다.
    """
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
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    raise AgentOutputError("모델 응답에 tool_use 블록이 없습니다(구조화 출력 실패).")
