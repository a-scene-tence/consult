"""numeric_guard — 숫자 환각 방지 훅 (CLAUDE.md §3.2, SPEC §4.1).

Strict Rule의 최후 방어선. 에이전트 출력에 등장하는 모든 **재무 수치**가 확장된 Golden Set에
**허용 오차 0**으로 존재하는지 대조한다. 하나라도 불일치하면 환각으로 간주한다.

**확장 Golden Set(v0.3, CLAUDE §3.2):** 다음 소스를 모두 재귀 탐색해 수치를 모은다 —
1. `financials.pl` / `financials.bs` (품목 마진·BEP·CCC·DSCR 등 파생 지표 포함),
2. `client_profile` (예: `owner_age`=34 → Golden Set에 34 포함),
3. `followup_context` (특히 `metric_progress`의 직전/당기 확정치, `previous_recommendations`의 rec_code).

`golden_inputs`는 dict 하나 또는 여러 dict의 리스트를 받는다(리스트 권장 — `golden_sources()` 사용).

핵심 원리
---------
1. `golden_inputs`(dict 또는 dict 리스트)를 재귀 탐색하여 모든 숫자를 정규화해 Golden Set을 만든다.
2. `agent_output_json`을 재귀 탐색하여 모든 필드(분석 텍스트 포함)에서 숫자를 추출한다.
3. 추출한 숫자 중 **재무적 수치**만 Golden Set과 대조한다. 비재무 수치(연도·분기·서수·
   코드에 포함된 숫자)는 예외 처리하여 오탐을 막는다.
   주의: 나이 단위 '세'·'살'은 비재무 단위로 두지 **않는다** — 가짜 나이("99세")를 반드시 적발하기 위함.

포맷팅 정규화
-------------
- 천단위 쉼표(`120,000,000`)와 통화/퍼센트 기호(`%`, `원`, `₩`)를 제거해 값만 비교한다.
- `14.2`와 `14.20`, `20`과 `20.0`은 Decimal 값 동등성으로 동일 취급(오차 0).

예외 처리(비재무 수치)
----------------------
- **코드/식별자:** ASCII 문자에 인접한 숫자(예: `M1`, `Q2`, `OPM3`)는 계정/코드로 보고 제외.
- **연도:** 4자리 정수 1900~2100 (뒤에 재무 단위가 붙지 않을 때).
- **분기·서수·기간 단위:** 숫자 뒤에 `분기/차/개/번째/위/년/월/일/명/회/건/주`가 오면 제외.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# 숫자 토큰: 천단위 쉼표 그룹 또는 일반 정수/소수.
_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

# 재무 단위(뒤에 오면 반드시 Golden Set 대조). "%p"(퍼센트포인트)는 "%"로 포착된다.
_FIN_UNITS: tuple[str, ...] = ("%", "원", "₩", "억", "만", "천", "조", "배")
# 비재무 단위(뒤에 오면 제외).
_NONFIN_UNITS: tuple[str, ...] = (
    "분기", "차", "개", "번째", "위", "년", "월", "일", "명", "회", "건", "주",
)
# Golden Set 구성 시 잡음이 큰(타임스탬프) 키의 문자열 수치는 제외.
_SKIP_STRING_KEYS: frozenset[str] = frozenset(
    {"computed_at", "embedded_at", "retrieved_at", "created_at", "published_at"}
)


class NumericGuardViolation(Exception):
    """에이전트 출력에서 환각 수치가 감지되었을 때 발생."""


def _to_decimal(raw: str) -> Decimal | None:
    """쉼표·기호를 제거한 숫자 문자열을 Decimal로. 실패 시 None."""
    cleaned = raw.replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _is_code_char(ch: str) -> bool:
    """ASCII 알파벳 여부(코드/식별자 판별용). 한글은 제외한다."""
    return ch.isascii() and ch.isalpha()


def _raw_numbers(text: str) -> list[Decimal]:
    """문자열에서 모든 숫자를 추출(Golden Set 구성용, 예외 없음)."""
    out: list[Decimal] = []
    for m in _NUMBER_RE.finditer(text):
        d = _to_decimal(m.group())
        if d is not None:
            out.append(d)
    return out


def _financial_numbers(text: str) -> list[Decimal]:
    """문자열에서 **재무적 수치만** 추출(대조 대상). 비재무 수치는 예외 처리."""
    out: list[Decimal] = []
    for m in _NUMBER_RE.finditer(text):
        start, end = m.span()
        token = m.group()
        prev_char = text[start - 1] if start > 0 else ""
        next_char = text[end] if end < len(text) else ""
        tail = text[end : end + 3]

        # (1) 코드/식별자: 앞뒤로 ASCII 문자에 인접 → 제외 (M1, Q2 등)
        if _is_code_char(prev_char) or _is_code_char(next_char):
            continue

        # (2) 재무 단위가 뒤따르면 반드시 대조
        if tail.startswith(_FIN_UNITS):
            d = _to_decimal(token)
            if d is not None:
                out.append(d)
            continue

        # (3) 비재무 단위가 뒤따르면 제외 (2분기, 1차, 3개 ...)
        if tail.startswith(_NONFIN_UNITS):
            continue

        # (4) 연도(4자리 정수 1900~2100, 소수 아님) → 제외
        if "." not in token and "," not in token:
            d = _to_decimal(token)
            if d is not None and d == d.to_integral_value() and 1900 <= d <= 2100:
                continue

        # (5) 그 외 맨숫자 → 재무 수치로 간주하여 대조 (가짜 숫자 적발 지점)
        d = _to_decimal(token)
        if d is not None:
            out.append(d)
    return out


def _build_golden_set(obj: Any, key: str | None = None) -> set[Decimal]:
    """input_json을 재귀 탐색하여 허용 수치 집합을 만든다."""
    golden: set[Decimal] = set()
    if isinstance(obj, bool):
        return golden  # bool은 int 서브타입 — 수치로 취급하지 않음
    if isinstance(obj, (int, float)):
        d = _to_decimal(str(obj))
        if d is not None:
            golden.add(d)
    elif isinstance(obj, str):
        if key not in _SKIP_STRING_KEYS:
            golden.update(_raw_numbers(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            golden |= _build_golden_set(v, key=k)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            golden |= _build_golden_set(item, key=key)
    return golden


def _iter_output_financial_numbers(obj: Any) -> list[Decimal]:
    """agent_output_json을 재귀 탐색하여 대조 대상 재무 수치를 모은다."""
    found: list[Decimal] = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        d = _to_decimal(str(obj))
        if d is not None:
            found.append(d)
    elif isinstance(obj, str):
        found.extend(_financial_numbers(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_iter_output_financial_numbers(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found.extend(_iter_output_financial_numbers(item))
    return found


def golden_sources(
    financials: dict[str, Any],
    client_profile: dict[str, Any] | None = None,
    followup_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """확장 Golden Set 구성용 소스 리스트를 만든다(None 제외).

    에이전트 파이프라인에서 `numeric_guard(golden_sources(fin, profile, followup), output)`
    형태로 사용한다.
    """
    return [src for src in (financials, client_profile, followup_context) if src is not None]


def find_hallucinated_numbers(
    golden_inputs: dict[str, Any] | list[dict[str, Any]],
    agent_output_json: dict[str, Any],
) -> list[str]:
    """Golden Set에 없는 (= 환각으로 의심되는) 재무 수치 목록을 반환한다.

    `golden_inputs`는 dict 하나 또는 dict 리스트. 빈 리스트이면 환각 없음.
    보고/디버깅용으로 원본 값의 문자열 표현을 반환한다.
    """
    golden = _build_golden_set(golden_inputs)
    violations: list[str] = []
    for value in _iter_output_financial_numbers(agent_output_json):
        if value not in golden:
            violations.append(str(value))
    return violations


def numeric_guard(
    golden_inputs: dict[str, Any] | list[dict[str, Any]],
    agent_output_json: dict[str, Any],
) -> bool:
    """에이전트 출력의 모든 재무 수치가 확장 Golden Set에 존재하면 True.

    하나라도 불일치(환각)하면 False. 허용 오차 0. `golden_inputs`는 dict 또는 dict 리스트.
    """
    return not find_hallucinated_numbers(golden_inputs, agent_output_json)
