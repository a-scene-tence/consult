"""compute_bs v0.3 골든/경계 테스트 — 실전 현금흐름(CCC/DSCR/Runway).

기대값은 mock 입력에 CLAUDE §3.0 공식을 적용해 자체 산출한 값이다.
"""

from __future__ import annotations

import pytest

from compute._common import validate_payload
from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.ingest import sample_bs_raw, sample_pl_raw

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _pl_context():
    return pl_context_from_pl(compute_pl(sample_pl_raw(), computed_at=FIXED_TS))


def _bs(raw=None, pl_context=None):
    return compute_bs(
        raw or sample_bs_raw(),
        pl_context=pl_context or _pl_context(),
        computed_at=FIXED_TS,
    )


def test_bs_ratios():
    ratios = {r["code"]: r for r in _bs()["ratios"]}
    assert ratios["CR"]["value_pct"] == 153.2  # 95M/62M*100
    assert ratios["WC"]["value"] == 33_000_000  # 95M-62M
    assert ratios["DR"]["value_pct"] == 122.1  # 105M/86M*100


def test_bs_pl_context_extraction():
    """pl_context_from_pl 이 OP·OPEX·감가상각비를 정확히 추출한다."""
    ctx = _pl_context()
    assert ctx["operating_income"] == 18_500_000
    assert ctx["fixed_cost"] == 27_000_000
    assert ctx["depreciation"] == 2_000_000  # opex_details '감가상각비'


def test_bs_cash_flow_golden():
    cf = _bs()["cash_flow"]
    # 재고 가중평균일수 = (9M*6 + 4.5M*41)/13.5M = 17.666..; CCC = 17.67 + 33 - 28
    assert cf["cash_conversion_cycle_days"] == 22.7
    # DSCR = (18.5M + 2.0M) / (3.08M * 3개월) = 20.5M/9.24M = 2.218 → 2.22
    assert cf["dscr"] == 2.22
    assert cf["monthly_debt_service"] == 3_080_000  # 1.85M + 1.23M
    assert cf["monthly_fixed_cost"] == 9_000_000  # 27M / 3개월
    assert cf["cash_runway_months"] == 2.7  # 24M / 9M


def test_bs_owner_draws_and_details():
    bs = _bs()
    assert bs["owner_draws"]["suspense_receipts"] == 12_000_000
    assert bs["owner_draws"]["suspense_payments"] == 3_500_000
    assert len(bs["debt_details"]) == 2
    assert len(bs["upcoming_tax_events"]) >= 1  # 세무 이벤트 병합됨


def test_bs_schema_contract():
    payload = _bs()
    assert validate_payload(payload, "financials_bs") is payload


def test_bs_deterministic():
    assert _bs() == _bs()


def test_bs_missing_pl_context_key_raises():
    with pytest.raises(ValueError, match="pl_context"):
        compute_bs(sample_bs_raw(), pl_context={"operating_income": 1.0}, computed_at=FIXED_TS)


def test_bs_zero_equity_raises():
    raw = sample_bs_raw()
    raw["accounts"]["EQUITY"]["amount"] = 0
    with pytest.raises(ValueError, match="부채비율"):
        _bs(raw=raw)


def test_bs_zero_cl_raises():
    raw = sample_bs_raw()
    raw["accounts"]["CL"]["amount"] = 0
    with pytest.raises(ValueError, match="유동비율"):
        _bs(raw=raw)


def test_bs_no_debt_raises_dscr():
    """월 원리금 총액 0 → DSCR 계산 불가(division_by_zero 방어)."""
    raw = sample_bs_raw()
    raw["debt_details"] = []
    with pytest.raises(ValueError, match="DSCR"):
        _bs(raw=raw)
