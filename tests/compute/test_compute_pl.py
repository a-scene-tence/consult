"""compute_pl 골든 케이스 및 계약 테스트.

기대값은 SPEC §2.6 의 financials.pl 예시와 일치해야 한다.
"""

from __future__ import annotations

import pytest

from compute._common import validate_payload
from compute.compute_pl import compute_pl
from compute.ingest import sample_pl_raw

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _accounts_by_code(payload):
    return {a["code"]: a for a in payload["accounts"]}


def _ratios_by_code(payload):
    return {r["code"]: r for r in payload["ratios"]}


def test_pl_golden_accounts():
    """파생 계정(GP/OP)과 전기대비 증감률(yoy_pct)이 골든값과 일치한다."""
    payload = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    acc = _accounts_by_code(payload)

    assert acc["GP"]["amount"] == 42_000_000
    assert acc["OP"]["amount"] == 17_000_000

    assert acc["REV"]["yoy_pct"] == 20.0
    assert acc["COGS"]["yoy_pct"] == 11.4
    assert acc["GP"]["yoy_pct"] == 40.0
    assert acc["OPEX"]["yoy_pct"] == 13.6
    assert acc["OP"]["yoy_pct"] == 112.5


def test_pl_golden_ratios():
    """수익성 비율(GPM/OPM)이 골든값과 일치한다. OPM 은 14.166..%→14.2 반올림."""
    payload = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    ratios = _ratios_by_code(payload)

    assert ratios["GPM"]["value_pct"] == 35.0
    assert ratios["GPM"]["prev_value_pct"] == 30.0
    assert ratios["OPM"]["value_pct"] == 14.2
    assert ratios["OPM"]["prev_value_pct"] == 8.0


def test_pl_golden_milestone():
    payload = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    m = payload["milestones"][0]
    assert m["code"] == "M1"
    assert m["variance_pct"] == 4.0


def test_pl_schema_contract():
    """산출물이 financials_pl JSON Schema 를 통과한다."""
    payload = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    # 예외가 없으면 통과. 반환값 동일성도 확인.
    assert validate_payload(payload, "financials_pl") is payload


def test_pl_deterministic():
    """동일 입력 + 동일 computed_at → 동일 출력(결정론)."""
    a = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    b = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    assert a == b


def test_pl_missing_required_account_raises():
    raw = sample_pl_raw()
    del raw["accounts"]["COGS"]
    with pytest.raises(ValueError, match="COGS"):
        compute_pl(raw, computed_at=FIXED_TS)


def test_pl_zero_prev_raises():
    """전기 금액 0 → 증감률 계산 불가로 실패해야 한다."""
    raw = sample_pl_raw()
    raw["accounts"]["REV"]["prev_amount"] = 0
    with pytest.raises(ValueError, match="yoy_pct"):
        compute_pl(raw, computed_at=FIXED_TS)
