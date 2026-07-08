"""compute_bs — BS 확정 수치 계산 (v0.3 실전 현금흐름).

CLAUDE.md §0-1(Strict Rule): 재무 산술이 확정되는 유일 지점.
입력(전문가 정제 Master raw) + pl_context(PL 파생값) → `financials.bs` JSON(스키마 검증) 반환.

핵심 산출(CLAUDE §3.0):
- CCC(현금전환주기) = 재고일수(금액 가중평균) + AR일수 − AP일수
- DSCR = (영업이익 + 감가상각비) ÷ 기간 원리금 총액
- Cash Runway = 가용현금(CASH) ÷ 월 고정비
- 세무 캘린더 대조(upcoming_tax_events)

pl_context: {operating_income, depreciation, fixed_cost}. 오케스트레이터가 compute_pl 결과에서
전달한다(감가상각비는 opex_details에서 식별 — `pl_context_from_pl`). 테스트는 명시 주입.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from compute._common import round_pct, to_money, validate_payload
from compute.tax_calendar import months_in_period, upcoming_tax_events

_ACCOUNT_ORDER = ["CASH", "CA", "CL", "INV", "AR", "AP", "DEBT", "EQUITY"]
_REQUIRED_BASE = ("CASH", "CA", "CL", "DEBT", "EQUITY")

# 감가상각비로 식별할 판관비 계정명 키워드(CLAUDE §3.0).
_DEPRECIATION_KEYS = ("감가상각",)


def pl_context_from_pl(financials_pl: dict[str, Any]) -> dict[str, float]:
    """compute_pl 결과에서 compute_bs 가 필요로 하는 파생값을 추출한다.

    - operating_income: accounts 의 OP 금액
    - fixed_cost: accounts 의 OPEX 금액
    - depreciation: opex_details 중 계정명에 '감가상각'이 포함된 항목 합계
    """
    accounts = {a["code"]: a["amount"] for a in financials_pl.get("accounts", [])}
    depreciation = sum(
        float(od["amount"])
        for od in financials_pl.get("opex_details", [])
        if any(k in od["account_name"] for k in _DEPRECIATION_KEYS)
    )
    return {
        "operating_income": float(accounts.get("OP", 0.0)),
        "fixed_cost": float(accounts.get("OPEX", 0.0)),
        "depreciation": float(depreciation),
    }


def compute_bs(
    raw: dict[str, Any],
    *,
    pl_context: dict[str, float],
    computed_at: str | None = None,
) -> dict[str, Any]:
    """BS Master raw + pl_context 로부터 financials.bs 확정 JSON을 산출한다."""
    meta_in = raw["meta"]
    base = raw["accounts"]
    for code in _REQUIRED_BASE:
        if code not in base:
            raise ValueError(f"BS raw 데이터에 필수 계정 '{code}' 이(가) 없습니다.")
    for key in ("operating_income", "depreciation", "fixed_cost"):
        if key not in pl_context:
            raise ValueError(f"pl_context 에 '{key}' 이(가) 없습니다(PL 파생값 필요).")

    amount = {c: float(base[c]["amount"]) for c in base}
    order = [c for c in _ACCOUNT_ORDER if c in base]
    accounts = [
        {"code": c, "name": base[c]["name"], "amount": to_money(amount[c])} for c in order
    ]

    if amount["CL"] == 0:
        raise ValueError("유동부채(CL)가 0이라 유동비율(CR)을 계산할 수 없습니다.")
    if amount["EQUITY"] == 0:
        raise ValueError("자본총계(EQUITY)가 0이라 부채비율(DR)을 계산할 수 없습니다.")

    ratios = [
        {"code": "CR", "name": "유동비율", "value_pct": round_pct(amount["CA"] / amount["CL"] * 100)},
        {"code": "WC", "name": "운전자본", "value": to_money(amount["CA"] - amount["CL"])},
        {"code": "DR", "name": "부채비율", "value_pct": round_pct(amount["DEBT"] / amount["EQUITY"] * 100)},
    ]

    inventory_details = [
        {
            "category": it["category"],
            "amount": to_money(it["amount"]),
            "days_in_inventory": round_pct(float(it["days_in_inventory"])),
        }
        for it in raw.get("inventory_details", [])
    ]
    debt_details = [
        {
            "lender": d["lender"],
            "amount": to_money(d["amount"]),
            "interest_rate": round_pct(float(d["interest_rate"])),
            "monthly_payment": to_money(d["monthly_payment"]),
        }
        for d in raw.get("debt_details", [])
    ]
    wc = raw["working_capital_details"]
    working_capital_details = {
        "ar_days": round_pct(float(wc["ar_days"])),
        "ap_days": round_pct(float(wc["ap_days"])),
    }
    od = raw["owner_draws"]
    owner_draws = {
        "suspense_receipts": to_money(od["suspense_receipts"]),
        "suspense_payments": to_money(od["suspense_payments"]),
    }

    cash_flow = _compute_cash_flow(
        raw=raw,
        cash=amount["CASH"],
        ar_days=float(wc["ar_days"]),
        ap_days=float(wc["ap_days"]),
        period=meta_in["period"],
        pl_context=pl_context,
    )

    payload = {
        "meta": {
            "client_id": meta_in["client_id"],
            "period": meta_in["period"],
            "currency": meta_in["currency"],
            "computed_at": computed_at or datetime.now(timezone.utc).isoformat(),
        },
        "accounts": accounts,
        "inventory_details": inventory_details,
        "debt_details": debt_details,
        "working_capital_details": working_capital_details,
        "owner_draws": owner_draws,
        "ratios": ratios,
        "cash_flow": cash_flow,
        "upcoming_tax_events": upcoming_tax_events(meta_in["period"]),
    }
    return validate_payload(payload, "financials_bs")


def _weighted_inventory_days(items: list[dict[str, Any]]) -> float:
    """재고 카테고리별 일수를 금액 가중평균 (CCC 입력용)."""
    total = sum(float(it["amount"]) for it in items)
    if total == 0:
        return 0.0
    weighted = sum(float(it["amount"]) * float(it["days_in_inventory"]) for it in items)
    return weighted / total


def _compute_cash_flow(
    *,
    raw: dict[str, Any],
    cash: float,
    ar_days: float,
    ap_days: float,
    period: str,
    pl_context: dict[str, float],
) -> dict[str, Any]:
    """CCC·DSCR·Runway 산출."""
    inventory_days = _weighted_inventory_days(raw.get("inventory_details", []))
    ccc = inventory_days + ar_days - ap_days

    months = months_in_period(period)
    fixed_cost = pl_context["fixed_cost"]
    monthly_fixed_cost = fixed_cost / months
    if monthly_fixed_cost == 0:
        raise ValueError("월 고정비가 0이라 Cash Runway 를 계산할 수 없습니다.")
    cash_runway_months = cash / monthly_fixed_cost

    monthly_debt_service = sum(
        float(d["monthly_payment"]) for d in raw.get("debt_details", [])
    )
    period_debt_service = monthly_debt_service * months
    if period_debt_service == 0:
        raise ValueError("기간 원리금 총액이 0이라 DSCR 을 계산할 수 없습니다.")
    dscr = (pl_context["operating_income"] + pl_context["depreciation"]) / period_debt_service

    return {
        "cash_conversion_cycle_days": round_pct(ccc),
        "dscr": round(dscr, 2),
        "monthly_debt_service": to_money(monthly_debt_service),
        "monthly_fixed_cost": to_money(monthly_fixed_cost),
        "cash_runway_months": round_pct(cash_runway_months),
    }
