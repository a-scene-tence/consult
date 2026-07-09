"""리컨실러/어댑터 골든 테스트 (Phase 9).

Python 집계(REV/COGS/OPEX/DEBT)·미매칭현금 도출·총합 대사(reconciliation)·schema_extensions 보존을
검증하고, 산출 초안이 compute_pl/compute_bs 를 통과하는지 확인한다(assist→게이트→compute).
"""

from __future__ import annotations

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from compute.reconcile import build_draft_master


def _parser_output(source_raw_sum: int):
    sales = [
        {"item_name": "후라이드치킨", "selling_price": 18000, "unit_cost": 7200, "quantity": 3900},
        {"item_name": "콜라(병)", "selling_price": 2000, "unit_cost": 1400, "quantity": 5200},
    ]
    return {
        "info": {"client_id": "C-1001", "period": "2025-Q3", "business_days": 78,
                 "total_revenue": source_raw_sum},
        "sales": sales,
        "cogs": [{"material_category": "생닭", "amount": 41000000, "prev_amount": 33000000}],
        "opex": [{"account_name": "임차료", "amount": 6000000, "prev_amount": 6000000},
                 {"account_name": "감가상각비", "amount": 2000000, "prev_amount": 1800000}],
        "wc": {"ar_days": 33.0, "ap_days": 28.0},
        "inv": [{"category": "포장재", "amount": 4500000, "days_in_inventory": 41.0}],
        "debt": [{"lender": "○○은행", "amount": 60000000, "interest_rate": 5.2, "monthly_payment": 1850000},
                 {"lender": "△△캐피탈", "amount": 25000000, "interest_rate": 9.8, "monthly_payment": 1230000}],
        "od": {"suspense_receipts": 12000000, "suspense_payments": 3500000},
        "bs": {"total_cash": 24000000, "total_ca": 95000000, "total_cl": 62000000, "total_equity": 86000000},
        "schema_extensions": [{"target_sheet": "OPEX", "generated_key": "government_subsidy",
                               "korean_name": "손실보전금", "value": 3000000}],
        "raw_total_check": {"source_raw_sum": source_raw_sum},
    }


# 품목 매출 = 18000*3900 + 2000*5200 = 70,200,000 + 10,400,000 = 80,600,000
_ITEM_REV = 80_600_000


def test_aggregation_and_reconciliation_matched():
    po = _parser_output(_ITEM_REV + 9_100_000)  # 미매칭 현금 9.1M
    d = build_draft_master(po)
    r = d["reconciliation"]
    assert r["matched"] is True and r["reconciliation_error"] is False
    assert r["item_revenue"] == _ITEM_REV
    assert r["unallocated_cash_sales"] == 9_100_000
    # Python 집계
    assert d["pl_raw"]["accounts"]["REV"]["amount"] == _ITEM_REV + 9_100_000
    assert d["pl_raw"]["accounts"]["OPEX"]["amount"] == 8_000_000       # 6M + 2M
    assert d["bs_raw"]["accounts"]["DEBT"]["amount"] == 85_000_000      # 60M + 25M (집계)
    assert d["pl_raw"]["unallocated_cash_sales"]["amount"] == 9_100_000


def test_schema_extensions_preserved_not_injected():
    d = build_draft_master(_parser_output(_ITEM_REV))
    assert d["schema_extensions"][0]["generated_key"] == "government_subsidy"
    # compute 로 유입되는 pl_raw/bs_raw 에는 확장 키가 섞이지 않는다.
    assert "government_subsidy" not in str(d["pl_raw"])


def test_reconciliation_error_when_items_exceed_raw():
    """품목 매출이 원시 총합을 초과하면 reconciliation_error(환각/파싱 오류)."""
    po = _parser_output(_ITEM_REV - 1_000_000)  # 원시 총합 < 품목 매출
    d = build_draft_master(po)
    assert d["reconciliation"]["matched"] is False
    assert "item_revenue_exceeds_raw_total" in d["reconciliation"]["errors"]


def test_draft_feeds_compute():
    """산출 초안이 compute_pl/compute_bs 를 통과한다(전문가 승인 후 유입 경로)."""
    d = build_draft_master(_parser_output(_ITEM_REV + 9_100_000))
    pl = compute_pl(d["pl_raw"], computed_at="2026-07-07T09:00:00+09:00")
    bs = compute_bs(d["bs_raw"], pl_context=pl_context_from_pl(pl), computed_at="2026-07-07T09:00:00+09:00")
    assert pl["bep"]["bep_revenue"] > 0
    assert bs["cash_flow"]["cash_runway_months"] > 0
