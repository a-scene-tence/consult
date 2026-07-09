"""리컨실러/어댑터 (Phase 9) — parser_output → 표준화 Master 초안 + 총합 대사.

Agent 0(데이터 엔지니어)이 파싱한 `parser_output` 을 받아, **모든 산술을 Python 에서 확정**하여
(Strict Rule §0.1) compute 가 소비하는 Master raw(`pl_raw`/`bs_raw`) **초안**을 만든다. 동시에 원시
총합 대사(reconciliation)를 수행해 환각/파싱 오류를 잡는다(`reconciliation_error`).

이 산출물은 **전문가 검토용 초안**이다 — 전문가가 warnings(누락 전기 등)를 보완·승인한 뒤에만
기존 `POST /api/consulting/ingest` 로 유입된다(§0.5 전문가 게이트 보존). schema_extensions 는 표준에
매핑되지 않은 데이터이므로 **그대로 보존**하고 compute 에 자동 주입하지 않는다.
"""

from __future__ import annotations

from typing import Any

from compute._common import to_money

# 대사 허용 오차(원). LLM 이 보고한 두 총합(info.total_revenue vs source_raw_sum)의 미세 차이 허용.
_RECON_TOLERANCE = 0


def _sum(items: list[dict[str, Any]], key: str) -> int:
    return to_money(sum(to_money(it.get(key, 0)) for it in items))


def _sum_prev(items: list[dict[str, Any]]) -> int | None:
    """모든 항목에 prev_amount 가 있으면 합, 하나라도 없으면 None(전문가 보완 대상)."""
    if items and all("prev_amount" in it and it["prev_amount"] is not None for it in items):
        return to_money(sum(to_money(it["prev_amount"]) for it in items))
    return None


def _reconcile(parser_output: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """원시 총합 대사 — 파싱 항목 총합과 원시 총합(source_raw_sum)을 대조한다."""
    sales = parser_output["sales"]
    item_revenue = to_money(
        sum(to_money(s["selling_price"]) * to_money(s["quantity"]) for s in sales)
    )
    source_raw_sum = to_money(parser_output["raw_total_check"]["source_raw_sum"])
    reported_total = to_money(parser_output["info"]["total_revenue"])
    unallocated = source_raw_sum - item_revenue

    errors: list[str] = []
    # (1) 품목 매출이 원시 총합을 초과 → 파싱 오류/환각(불가능).
    if unallocated < 0:
        errors.append("item_revenue_exceeds_raw_total")
    # (2) LLM 이 보고한 두 총합 불일치 → 추출 일관성 경고.
    if abs(reported_total - source_raw_sum) > _RECON_TOLERANCE:
        warnings.append(
            f"reported_total({reported_total}) != source_raw_sum({source_raw_sum})"
        )

    return {
        "matched": not errors,
        "reconciliation_error": bool(errors),
        "errors": errors,
        "source_raw_sum": source_raw_sum,
        "item_revenue": item_revenue,
        "reported_total": reported_total,
        "unallocated_cash_sales": max(unallocated, 0),
    }


def build_draft_master(parser_output: dict[str, Any]) -> dict[str, Any]:
    """parser_output → {pl_raw, bs_raw, reconciliation, schema_extensions, warnings}.

    모든 집계는 Python 이 확정한다(REV=Σ(P×Q)+미매칭현금, COGS/OPEX=Σamount, DEBT=Σdebt.amount).
    전기(prev) 미제공 항목은 placeholder(당기값) + warning 으로 표기해 전문가가 보완하게 한다.
    """
    warnings: list[str] = []
    recon = _reconcile(parser_output, warnings)

    info = parser_output["info"]
    sales = parser_output["sales"]
    cogs = parser_output["cogs"]
    opex = parser_output["opex"]

    # --- 집계(Python 확정) ---
    rev_amount = recon["source_raw_sum"]
    cogs_amount = _sum(cogs, "amount")
    opex_amount = _sum(opex, "amount")
    cogs_prev = _sum_prev(cogs)
    opex_prev = _sum_prev(opex)

    # 전기 미제공 시 placeholder(당기값) + 경고(전문가 보완).
    if cogs_prev is None:
        cogs_prev = cogs_amount
        warnings.append("COGS.prev_amount 미제공 — placeholder(당기값), 전문가 보완 필요")
    if opex_prev is None:
        opex_prev = opex_amount
        warnings.append("OPEX.prev_amount 미제공 — placeholder(당기값), 전문가 보완 필요")
    warnings.append("REV.prev_amount 미제공 — placeholder(당기값), 전문가 보완 필요")

    pl_raw = {
        "meta": {
            "client_id": info["client_id"],
            "period": info["period"],
            "prev_period": None,
            "currency": "KRW",
            "business_days": info["business_days"],
        },
        "accounts": {
            "REV": {"name": "매출액", "amount": rev_amount, "prev_amount": rev_amount},
            "COGS": {"name": "매출원가", "amount": cogs_amount, "prev_amount": cogs_prev},
            "OPEX": {"name": "판매관리비", "amount": opex_amount, "prev_amount": opex_prev},
        },
        "unallocated_cash_sales": {"amount": recon["unallocated_cash_sales"]},
        "sales_details": [
            {
                "item_name": s["item_name"], "selling_price": to_money(s["selling_price"]),
                "unit_cost": to_money(s["unit_cost"]), "quantity": to_money(s["quantity"]),
            }
            for s in sales
        ],
        "cogs_details": [
            {
                "material_category": c["material_category"], "amount": to_money(c["amount"]),
                "prev_amount": to_money(c.get("prev_amount", c["amount"])),
            }
            for c in cogs
        ],
        "opex_details": [
            {
                "account_name": o["account_name"], "amount": to_money(o["amount"]),
                "prev_amount": to_money(o.get("prev_amount", o["amount"])),
            }
            for o in opex
        ],
        "milestones": [],
    }

    # --- BS: DEBT 는 debt[] 합산(Python) ---
    debt = parser_output["debt"]
    bs = parser_output["bs"]
    debt_total = _sum(debt, "amount")
    bs_raw = {
        "meta": {"client_id": info["client_id"], "period": info["period"], "currency": "KRW"},
        "accounts": {
            "CASH": {"name": "현금및예금", "amount": to_money(bs["total_cash"])},
            "CA": {"name": "유동자산", "amount": to_money(bs["total_ca"])},
            "CL": {"name": "유동부채", "amount": to_money(bs["total_cl"])},
            "DEBT": {"name": "총부채", "amount": debt_total},
            "EQUITY": {"name": "자본총계", "amount": to_money(bs["total_equity"])},
        },
        "inventory_details": [
            {
                "category": i["category"], "amount": to_money(i["amount"]),
                "days_in_inventory": float(i["days_in_inventory"]),
            }
            for i in parser_output["inv"]
        ],
        "debt_details": [
            {
                "lender": d["lender"], "amount": to_money(d["amount"]),
                "interest_rate": float(d["interest_rate"]),
                "monthly_payment": to_money(d["monthly_payment"]),
            }
            for d in debt
        ],
        "working_capital_details": {
            "ar_days": float(parser_output["wc"]["ar_days"]),
            "ap_days": float(parser_output["wc"]["ap_days"]),
        },
        "owner_draws": {
            "suspense_receipts": to_money(parser_output["od"]["suspense_receipts"]),
            "suspense_payments": to_money(parser_output["od"]["suspense_payments"]),
        },
    }

    return {
        "pl_raw": pl_raw,
        "bs_raw": bs_raw,
        "reconciliation": recon,
        # schema_extensions 는 표준 밖 데이터 — compute 자동 주입 금지, 전문가 판단 대상.
        "schema_extensions": parser_output.get("schema_extensions", []),
        "warnings": warnings,
    }
