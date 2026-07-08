"""compute_pl — PL 확정 수치 계산 (v0.3 미시 분석 + BEP).

CLAUDE.md §0-1(Strict Rule): 재무 산술이 확정되는 유일 지점.
입력(전문가 정제 Master raw) → Pandas/순수 Python 연산 → `financials.pl` JSON(스키마 검증) 반환.

핵심 산출(CLAUDE §3.0 의무 목록):
- 품목별 unit_margin·margin_pct·contribution_margin·margin_rank(best/mid/worst)
- 판관비 증가율 Top3(increase_rank)
- 현금 매출 누락(unallocated_cash_sales.risk_flag)
- 손익분기점 BEP(공헌이익률·bep_revenue·bep_attainment·anchor_item·daily_target_qty)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from compute._common import round_pct, to_money, validate_payload, yoy_pct

_DERIVED_NAMES = {"GP": "매출총이익", "OP": "영업이익"}
_ACCOUNT_ORDER = ["REV", "COGS", "GP", "OPEX", "OP"]
_REQUIRED_BASE = ("REV", "COGS", "OPEX")

# 현금 매출 누락 비중 임계값(%) — risk_flag 판정. (.env 로 이관 가능, 기본 보수적.)
_UNALLOC_WARN_PCT = 5.0
_UNALLOC_CRITICAL_PCT = 10.0
# 판관비 증가율 상위 몇 개까지 increase_rank 를 매길지.
_OPEX_TOP_N = 3


def compute_pl(raw: dict[str, Any], *, computed_at: str | None = None) -> dict[str, Any]:
    """전문가 정제 Master raw 로부터 financials.pl 확정 JSON을 산출한다."""
    meta_in = raw["meta"]
    base = raw["accounts"]
    for code in _REQUIRED_BASE:
        if code not in base:
            raise ValueError(f"PL raw 데이터에 필수 계정 '{code}' 이(가) 없습니다.")
    business_days = int(meta_in["business_days"])
    if business_days <= 0:
        raise ValueError("business_days 가 0 이하라 일일 타겟 수량을 계산할 수 없습니다.")

    # --- 1) 기본 계정 + 파생(GP/OP) + 전기대비 증감률 ---
    amount = pd.Series({c: float(base[c]["amount"]) for c in base})
    prev = pd.Series({c: float(base[c]["prev_amount"]) for c in base})
    amount["GP"] = amount["REV"] - amount["COGS"]
    prev["GP"] = prev["REV"] - prev["COGS"]
    amount["OP"] = amount["GP"] - amount["OPEX"]
    prev["OP"] = prev["GP"] - prev["OPEX"]
    names = {c: base[c]["name"] for c in base} | _DERIVED_NAMES

    accounts = [
        {
            "code": c,
            "name": names[c],
            "amount": to_money(amount[c]),
            "prev_amount": to_money(prev[c]),
            "yoy_pct": yoy_pct(amount[c], prev[c]),
        }
        for c in _ACCOUNT_ORDER
    ]

    rev = amount["REV"]
    if rev == 0:
        raise ValueError("매출액(REV)이 0이라 비율·BEP를 계산할 수 없습니다.")

    # --- 2) 수익성 비율 ---
    ratios = [
        {
            "code": "GPM",
            "name": "매출총이익률",
            "value_pct": round_pct(amount["GP"] / rev * 100),
            "prev_value_pct": round_pct(prev["GP"] / prev["REV"] * 100) if prev["REV"] else 0.0,
        },
        {
            "code": "OPM",
            "name": "영업이익률",
            "value_pct": round_pct(amount["OP"] / rev * 100),
            "prev_value_pct": round_pct(prev["OP"] / prev["REV"] * 100) if prev["REV"] else 0.0,
        },
    ]

    # --- 3) 품목별 P*Q (Pandas) ---
    sales_details, sum_contribution, sum_pq, anchor = _compute_sales_details(
        raw.get("sales_details", [])
    )

    # --- 4) COGS 세부 (전기대비) ---
    cogs_details = [
        {
            "material_category": row["material_category"],
            "amount": to_money(row["amount"]),
            "prev_amount": to_money(row["prev_amount"]),
            "yoy_pct": yoy_pct(float(row["amount"]), float(row["prev_amount"])),
        }
        for row in raw.get("cogs_details", [])
    ]

    # --- 5) 판관비 세부 + 증가율 Top3 ---
    opex_details = _compute_opex_details(raw.get("opex_details", []))

    # --- 6) 현금 매출 누락 ---
    unalloc_amount = float(raw.get("unallocated_cash_sales", {}).get("amount", 0))
    unalloc_pct = round_pct(unalloc_amount / rev * 100)
    unallocated = {
        "amount": to_money(unalloc_amount),
        "pct_of_revenue": unalloc_pct,
        "risk_flag": _risk_flag(unalloc_pct),
    }

    # --- 7) 손익분기점 BEP ---
    bep = _compute_bep(
        sum_contribution=sum_contribution,
        sum_pq=sum_pq,
        fixed_cost=amount["OPEX"],
        revenue=rev,
        anchor=anchor,
        business_days=business_days,
    )

    # --- 8) 마일스톤 ---
    milestones = []
    for m in raw.get("milestones", []):
        if m["planned"] == 0:
            raise ValueError(f"마일스톤 '{m['code']}' 의 planned 가 0이라 variance 계산 불가.")
        milestones.append(
            {
                "code": m["code"],
                "name": m["name"],
                "planned": to_money(m["planned"]),
                "actual": to_money(m["actual"]),
                "variance_pct": round_pct((m["actual"] - m["planned"]) / m["planned"] * 100),
            }
        )

    payload = {
        "meta": {
            "client_id": meta_in["client_id"],
            "period": meta_in["period"],
            "prev_period": meta_in.get("prev_period"),
            "currency": meta_in["currency"],
            "business_days": business_days,
            "computed_at": computed_at or datetime.now(timezone.utc).isoformat(),
        },
        "accounts": accounts,
        "unallocated_cash_sales": unallocated,
        "sales_details": sales_details,
        "cogs_details": cogs_details,
        "opex_details": opex_details,
        "ratios": ratios,
        "bep": bep,
        "milestones": milestones,
    }
    return validate_payload(payload, "financials_pl")


def _compute_sales_details(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, float, dict[str, Any] | None]:
    """품목별 마진·공헌이익·margin_rank 산출. (details, Σ공헌이익, ΣPQ, anchor) 반환."""
    if not items:
        return [], 0.0, 0.0, None

    df = pd.DataFrame(items)
    if (df["selling_price"] == 0).any():
        raise ValueError("판매가(selling_price)가 0인 품목이 있어 마진율을 계산할 수 없습니다.")
    df["unit_margin"] = df["selling_price"] - df["unit_cost"]
    df["margin_pct"] = (df["unit_margin"] / df["selling_price"] * 100).round(1)
    df["contribution_margin"] = df["unit_margin"] * df["quantity"]

    # margin_rank: 마진율 최고=best, 최저=worst, 그 외 mid (CLAUDE §3.0 "마진율 정렬")
    best_idx = df["margin_pct"].idxmax()
    worst_idx = df["margin_pct"].idxmin()
    ranks = {}
    for idx in df.index:
        if idx == best_idx:
            ranks[idx] = "best"
        elif idx == worst_idx:
            ranks[idx] = "worst"
        else:
            ranks[idx] = "mid"
    # 품목이 1개면 best 로만 표기
    if len(df) == 1:
        ranks[df.index[0]] = "best"

    details = [
        {
            "item_name": row.item_name,
            "selling_price": to_money(row.selling_price),
            "unit_cost": to_money(row.unit_cost),
            "quantity": to_money(row.quantity),
            "unit_margin": to_money(row.unit_margin),
            "margin_pct": float(row.margin_pct),
            "contribution_margin": to_money(row.contribution_margin),
            "margin_rank": ranks[row.Index],
        }
        for row in df.itertuples()
    ]

    sum_contribution = float((df["unit_margin"] * df["quantity"]).sum())
    sum_pq = float((df["selling_price"] * df["quantity"]).sum())
    anchor_pos = df["contribution_margin"].idxmax()  # 공헌이익 최대 = 주력 상품
    anchor = {
        "item_name": df.loc[anchor_pos, "item_name"],
        "selling_price": float(df.loc[anchor_pos, "selling_price"]),
    }
    return details, sum_contribution, sum_pq, anchor


def _compute_opex_details(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """판관비 세부 + 증가율 Top3 increase_rank."""
    rows = [
        {
            "account_name": it["account_name"],
            "amount": to_money(it["amount"]),
            "prev_amount": to_money(it["prev_amount"]),
            "yoy_pct": yoy_pct(float(it["amount"]), float(it["prev_amount"])),
        }
        for it in items
    ]
    # 증가율 > 0 인 항목만 대상으로 내림차순 Top N 에 순위 부여
    increasing = sorted(
        [i for i, r in enumerate(rows) if r["yoy_pct"] > 0],
        key=lambda i: rows[i]["yoy_pct"],
        reverse=True,
    )
    rank_map = {idx: rank for rank, idx in enumerate(increasing[:_OPEX_TOP_N], start=1)}
    for i, r in enumerate(rows):
        r["increase_rank"] = rank_map.get(i)
    return rows


def _risk_flag(pct: float) -> str:
    if pct >= _UNALLOC_CRITICAL_PCT:
        return "critical"
    if pct >= _UNALLOC_WARN_PCT:
        return "warn"
    return "ok"


def _compute_bep(
    *,
    sum_contribution: float,
    sum_pq: float,
    fixed_cost: float,
    revenue: float,
    anchor: dict[str, Any] | None,
    business_days: int,
) -> dict[str, Any]:
    """손익분기점 산출. 가중 공헌이익률 = Σ(P−C)Q ÷ ΣPQ."""
    if not anchor or sum_pq == 0:
        raise ValueError("품목 매출(sales_details)이 없어 BEP 를 계산할 수 없습니다.")
    cm_ratio = sum_contribution / sum_pq  # 0~1
    if cm_ratio <= 0:
        raise ValueError("가중 공헌이익률이 0 이하라 BEP 매출액을 계산할 수 없습니다.")
    bep_revenue = fixed_cost / cm_ratio
    anchor_price = anchor["selling_price"]
    if anchor_price == 0:
        raise ValueError("주력 상품 판매가가 0이라 일일 타겟 수량을 계산할 수 없습니다.")
    daily_target_qty = math.ceil(bep_revenue / business_days / anchor_price)
    return {
        "contribution_margin_ratio_pct": round_pct(cm_ratio * 100),
        "fixed_cost": to_money(fixed_cost),
        "bep_revenue": to_money(bep_revenue),
        "bep_attainment_pct": round_pct(revenue / bep_revenue * 100),
        "anchor_item": anchor["item_name"],
        "daily_target_qty": int(daily_target_qty),
    }
