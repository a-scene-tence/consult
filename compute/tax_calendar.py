"""정적 세무 캘린더 (CLAUDE.md §3.0, SPEC §2.2).

리포트 기간(period)을 받아, 다가오는 주요 세무 일정까지 남은 개월 수(`months_until`)를
산출한다. 세무 일정은 **정적 설정**이며 Agent 2는 산출 결과(`upcoming_tax_events`)를 인용만 한다.

기준일 = 분석 period의 **종료일**. 기본 조회 horizon = 4개월.
"""

from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any

# 연간 반복되는 주요 세무 일정 (월, 일, 명칭). 세법 개정 시 이 표만 갱신한다.
_TAX_CALENDAR: tuple[tuple[int, int, str], ...] = (
    (1, 25, "부가가치세 확정신고(2기)"),
    (4, 25, "부가가치세 예정신고(1기)"),
    (5, 31, "종합소득세 확정신고"),
    (7, 25, "부가가치세 확정신고(1기)"),
    (10, 25, "부가가치세 예정신고(2기)"),
)

_DAYS_PER_MONTH = 30.44  # 평균(months_until 환산용)


def _period_end_date(period: str) -> date:
    """period 문자열의 종료일을 반환.

    지원: 'YYYY-Qn'(분기), 'YYYY-MM'(월), 'YYYY-Hn'(반기), 'YYYY'(연).
    """
    period = period.strip()
    year_str, _, rest = period.partition("-")
    year = int(year_str)

    if not rest:  # 'YYYY'
        return date(year, 12, 31)
    if rest.startswith("Q"):
        q = int(rest[1:])
        end_month = q * 3
    elif rest.startswith("H"):
        h = int(rest[1:])
        end_month = h * 6
    else:  # 'MM'
        end_month = int(rest)
    if not 1 <= end_month <= 12:
        raise ValueError(f"period 파싱 실패(월 범위): {period}")
    last_day = calendar.monthrange(year, end_month)[1]
    return date(year, end_month, last_day)


def upcoming_tax_events(period: str, horizon_months: float = 4.0) -> list[dict[str, Any]]:
    """period 종료일 기준 horizon_months 이내에 도래하는 세무 일정 목록.

    각 항목: {name, due_date(ISO), months_until(소수1자리)}. 도래일 오름차순.
    연말 경계(예: Q4 → 다음해 1월)를 위해 기준연도와 다음연도 인스턴스를 모두 생성한다.
    """
    ref = _period_end_date(period)
    events: list[dict[str, Any]] = []
    for year in (ref.year, ref.year + 1):
        for month, day, name in _TAX_CALENDAR:
            due = date(year, month, day)
            if due < ref:
                continue
            months_until = round((due - ref).days / _DAYS_PER_MONTH, 1)
            if months_until <= horizon_months:
                events.append(
                    {"name": name, "due_date": due.isoformat(), "months_until": months_until}
                )
    events.sort(key=lambda e: e["due_date"])
    return events


def months_in_period(period: str) -> int:
    """period가 포괄하는 개월 수 (Q=3, H=6, Y=12, 월=1)."""
    period = period.strip()
    _, _, rest = period.partition("-")
    if not rest:
        return 12
    if rest.startswith("Q"):
        return 3
    if rest.startswith("H"):
        return 6
    return 1
