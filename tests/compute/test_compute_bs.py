"""compute_bs 골든 케이스 및 계약 테스트.

기대값은 SPEC §2.6 의 financials.bs 예시와 일치해야 한다.
"""

from __future__ import annotations

import pytest

from compute._common import validate_payload
from compute.compute_bs import compute_bs
from compute.ingest import sample_bs_raw

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _ratios_by_code(payload):
    return {r["code"]: r for r in payload["ratios"]}


def test_bs_golden_ratios():
    """유동비율(CR)·운전자본(WC)·부채비율(DR)이 골든값과 일치한다."""
    payload = compute_bs(sample_bs_raw(), computed_at=FIXED_TS)
    ratios = _ratios_by_code(payload)

    assert ratios["CR"]["value_pct"] == 150.0
    assert ratios["WC"]["value"] == 30_000_000
    assert ratios["DR"]["value_pct"] == 137.5


def test_bs_accounts_preserved():
    """계정 금액은 raw 값 그대로 확정(재계산·왜곡 없음)."""
    payload = compute_bs(sample_bs_raw(), computed_at=FIXED_TS)
    acc = {a["code"]: a["amount"] for a in payload["accounts"]}
    assert acc["CA"] == 90_000_000
    assert acc["CL"] == 60_000_000
    assert acc["DEBT"] == 110_000_000
    assert acc["EQUITY"] == 80_000_000


def test_bs_schema_contract():
    payload = compute_bs(sample_bs_raw(), computed_at=FIXED_TS)
    assert validate_payload(payload, "financials_bs") is payload


def test_bs_deterministic():
    a = compute_bs(sample_bs_raw(), computed_at=FIXED_TS)
    b = compute_bs(sample_bs_raw(), computed_at=FIXED_TS)
    assert a == b


def test_bs_missing_required_account_raises():
    raw = sample_bs_raw()
    del raw["accounts"]["EQUITY"]
    with pytest.raises(ValueError, match="EQUITY"):
        compute_bs(raw, computed_at=FIXED_TS)


def test_bs_zero_equity_raises():
    """자본총계 0 → 부채비율 계산 불가로 실패해야 한다."""
    raw = sample_bs_raw()
    raw["accounts"]["EQUITY"]["amount"] = 0
    with pytest.raises(ValueError, match="부채비율"):
        compute_bs(raw, computed_at=FIXED_TS)
