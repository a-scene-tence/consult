"""compute_metrics — 시계열 이행 지표(metric_progress) 산출 (CLAUDE.md §3.0, SPEC §1.5).

직전 회차 확정치와 당기 확정치를 비교해 지표별 개선/악화(direction)를 산출한다.
**에이전트가 아니라 compute가 산출**하며, 결과는 numeric_guard Golden Set에 포함된다.

지표 극성(Polarity)은 하드코딩한다: 어떤 지표는 증가가 개선(OPM 등), 어떤 지표는 감소가
개선(ar_days·DR 등)이다.
"""

from __future__ import annotations

from typing import Any

# 지표 극성(하드코딩). higher_better = 증가 시 improved, lower_better = 감소 시 improved.
POLARITY: dict[str, str] = {
    # 높을수록 좋음
    "REV": "higher_better",
    "OP": "higher_better",
    "GPM": "higher_better",
    "OPM": "higher_better",
    "CR": "higher_better",
    "WC": "higher_better",
    "DSCR": "higher_better",
    "cash_runway_months": "higher_better",
    "bep_attainment_pct": "higher_better",
    "contribution_margin": "higher_better",
    # 낮을수록 좋음
    "DR": "lower_better",
    "ar_days": "lower_better",
    "cash_conversion_cycle_days": "lower_better",
    "unallocated_pct": "lower_better",
}

# 퍼센트(%) 지표 — metric_progress 에서 value_pct/prev_value_pct 키를 쓴다.
PCT_METRICS: frozenset[str] = frozenset(
    {"GPM", "OPM", "CR", "DR", "bep_attainment_pct", "unallocated_pct"}
)


def extract_pl_metrics(financials_pl: dict[str, Any]) -> dict[str, float]:
    """financials.pl 에서 추적 대상 지표를 {code: number} 로 추출."""
    out: dict[str, float] = {}
    for acc in financials_pl.get("accounts", []):
        if acc["code"] in ("REV", "OP"):
            out[acc["code"]] = float(acc["amount"])
    for r in financials_pl.get("ratios", []):
        out[r["code"]] = float(r["value_pct"])
    bep = financials_pl.get("bep")
    if bep:
        out["bep_attainment_pct"] = float(bep["bep_attainment_pct"])
    unalloc = financials_pl.get("unallocated_cash_sales")
    if unalloc:
        out["unallocated_pct"] = float(unalloc["pct_of_revenue"])
    return out


def extract_bs_metrics(financials_bs: dict[str, Any]) -> dict[str, float]:
    """financials.bs 에서 추적 대상 지표를 {code: number} 로 추출."""
    out: dict[str, float] = {}
    for r in financials_bs.get("ratios", []):
        if "value_pct" in r:
            out[r["code"]] = float(r["value_pct"])
        elif "value" in r:
            out[r["code"]] = float(r["value"])
    wc = financials_bs.get("working_capital_details")
    if wc:
        out["ar_days"] = float(wc["ar_days"])
    cf = financials_bs.get("cash_flow")
    if cf:
        out["dscr"] = float(cf["dscr"])
        out["cash_conversion_cycle_days"] = float(cf["cash_conversion_cycle_days"])
        out["cash_runway_months"] = float(cf["cash_runway_months"])
    return out


def _direction(code: str, prev: float, curr: float) -> str:
    if curr == prev:
        return "flat"
    improved_when_up = POLARITY[code] == "higher_better"
    went_up = curr > prev
    return "improved" if (went_up == improved_when_up) else "worsened"


def compute_metric_progress(
    prev_metrics: dict[str, float], curr_metrics: dict[str, float]
) -> list[dict[str, Any]]:
    """직전/당기 지표 dict를 비교해 metric_progress 배열을 산출한다.

    두 회차 모두에 존재하고 POLARITY에 정의된 지표만 대상. 극성에 따라
    improved/worsened/flat를 판정한다. 퍼센트 지표는 value_pct/prev_value_pct 키를 쓴다.
    """
    progress: list[dict[str, Any]] = []
    for code, curr in curr_metrics.items():
        if code not in prev_metrics or code not in POLARITY:
            continue
        prev = prev_metrics[code]
        item: dict[str, Any] = {"code": code, "direction": _direction(code, prev, curr)}
        if code in PCT_METRICS:
            item["prev_value_pct"] = prev
            item["value_pct"] = curr
        else:
            item["prev_value"] = prev
            item["value"] = curr
        progress.append(item)
    return progress
