"""compute_pl v0.3 골든/경계 테스트 — 미시 분석 + BEP.

기대값은 mock 입력(ingest.sample_pl_raw)에 CLAUDE §3.0 공식을 적용해 자체 산출한 값이다.
"""

from __future__ import annotations

import pytest

from compute._common import validate_payload
from compute.compute_pl import compute_pl
from compute.ingest import sample_pl_raw

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _pl():
    return compute_pl(sample_pl_raw(), computed_at=FIXED_TS)


def _accounts(payload):
    return {a["code"]: a for a in payload["accounts"]}


def _items(payload):
    return {s["item_name"]: s for s in payload["sales_details"]}


def test_pl_accounts_and_yoy():
    acc = _accounts(_pl())
    assert acc["GP"]["amount"] == 45_500_000
    assert acc["OP"]["amount"] == 18_500_000
    assert acc["REV"]["yoy_pct"] == 8.3  # (130-120)/120*100
    assert acc["OP"]["yoy_pct"] == 8.8  # (18.5-17)/17*100


def test_pl_ratios():
    ratios = {r["code"]: r for r in _pl()["ratios"]}
    assert ratios["GPM"]["value_pct"] == 35.0
    assert ratios["OPM"]["value_pct"] == 14.2


def test_pl_item_margins_and_rank():
    items = _items(_pl())
    assert items["후라이드치킨"]["margin_pct"] == 60.0
    assert items["후라이드치킨"]["margin_rank"] == "best"
    assert items["후라이드치킨"]["contribution_margin"] == 42_120_000  # (18000-7200)*3900
    assert items["콜라(병)"]["margin_rank"] == "worst"
    assert items["양념치킨"]["margin_rank"] == "mid"


def test_pl_opex_increase_rank_top3():
    opex = {o["account_name"]: o for o in _pl()["opex_details"]}
    assert opex["배달수수료"]["increase_rank"] == 1  # 20.6% 증가
    assert opex["감가상각비"]["increase_rank"] == 2  # 11.1% 증가
    assert opex["임차료"]["increase_rank"] is None  # 0% 증가 → 순위 없음


def test_pl_unallocated_cash_sales_risk_flag():
    u = _pl()["unallocated_cash_sales"]
    assert u["amount"] == 9_100_000
    assert u["pct_of_revenue"] == 7.0  # 9.1M/130M*100
    assert u["risk_flag"] == "warn"  # 5.0 <= 7.0 < 10.0


def test_pl_bep_golden():
    """BEP: 가중 공헌이익률·손익분기 매출액·일일 타겟 수량·달성률."""
    bep = _pl()["bep"]
    # Σ(P−C)Q = 42,120,000 + 21,840,000 + 3,120,000 = 67,080,000
    # ΣPQ     = 70,200,000 + 39,900,000 + 10,400,000 = 120,500,000
    # cm_ratio = 67,080,000/120,500,000 = 55.66% → 55.7
    assert bep["contribution_margin_ratio_pct"] == 55.7
    assert bep["fixed_cost"] == 27_000_000
    assert bep["bep_revenue"] == 48_501_789  # 27,000,000 / 0.556680...
    assert bep["bep_attainment_pct"] == 268.0  # 130M / 48.5M * 100
    assert bep["anchor_item"] == "후라이드치킨"  # 공헌이익 최대
    assert bep["daily_target_qty"] == 35  # ceil(48,501,789 / 78 / 18,000) = ceil(34.5)


def test_pl_schema_contract():
    payload = _pl()
    assert validate_payload(payload, "financials_pl") is payload


def test_pl_deterministic():
    assert compute_pl(sample_pl_raw(), computed_at=FIXED_TS) == compute_pl(
        sample_pl_raw(), computed_at=FIXED_TS
    )


def test_pl_zero_selling_price_raises():
    raw = sample_pl_raw()
    raw["sales_details"][0]["selling_price"] = 0
    with pytest.raises(ValueError, match="판매가"):
        compute_pl(raw, computed_at=FIXED_TS)


def test_pl_zero_business_days_raises():
    raw = sample_pl_raw()
    raw["meta"]["business_days"] = 0
    with pytest.raises(ValueError, match="business_days"):
        compute_pl(raw, computed_at=FIXED_TS)


def test_pl_all_quantity_zero_raises_bep():
    """모든 판매수량 0 → ΣPQ=0 → BEP 계산 불가(division_by_zero 방어)."""
    raw = sample_pl_raw()
    for item in raw["sales_details"]:
        item["quantity"] = 0
    with pytest.raises(ValueError, match="BEP"):
        compute_pl(raw, computed_at=FIXED_TS)
