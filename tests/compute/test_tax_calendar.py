"""tax_calendar — 정적 세무 캘린더 테스트."""

from __future__ import annotations

import pytest

from compute.tax_calendar import (
    _period_end_date,
    months_in_period,
    upcoming_tax_events,
)


def test_period_end_date_quarter():
    assert _period_end_date("2025-Q3").isoformat() == "2025-09-30"
    assert _period_end_date("2025-Q4").isoformat() == "2025-12-31"


def test_months_in_period():
    assert months_in_period("2025-Q3") == 3
    assert months_in_period("2025-H1") == 6
    assert months_in_period("2025") == 12
    assert months_in_period("2025-05") == 1


def test_upcoming_events_q3():
    """Q3(9/30 기준) 4개월 내: 10/25 예정신고, 2026-01-25 확정신고."""
    events = upcoming_tax_events("2025-Q3")
    names = [e["name"] for e in events]
    assert "부가가치세 예정신고(2기)" in names
    first = events[0]
    assert first["due_date"] == "2025-10-25"
    assert first["months_until"] == 0.8
    # 종소세(5/31)는 4개월 밖 → 제외
    assert all("종합소득세" not in n for n in names)


def test_year_boundary_q4():
    """Q4(12/31 기준) → 다음해 1/25 확정신고가 최근접."""
    events = upcoming_tax_events("2025-Q4")
    assert events[0]["due_date"] == "2026-01-25"
    assert events[0]["months_until"] == 0.8


def test_income_tax_appears_near_may():
    """Q1(3/31 기준) → 5/31 종소세가 약 2개월 뒤로 포착."""
    events = upcoming_tax_events("2025-Q1")
    names = [e["name"] for e in events]
    assert "종합소득세 확정신고" in names


def test_horizon_filters_far_events():
    """horizon 1개월로 좁히면 먼 이벤트는 제외."""
    near = upcoming_tax_events("2025-Q3", horizon_months=1.0)
    assert [e["due_date"] for e in near] == ["2025-10-25"]
