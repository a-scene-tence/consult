"""compute_metrics — metric_progress(시계열 이행) 테스트.

극성(Polarity) 딕셔너리에 따라 improved/worsened/flat 판정이 올바른지 검증한다.
"""

from __future__ import annotations

from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_metrics import (
    compute_metric_progress,
    extract_bs_metrics,
    extract_pl_metrics,
)
from compute.compute_pl import compute_pl
from compute.ingest import (
    sample_bs_raw,
    sample_pl_raw,
    sample_prev_bs_raw,
    sample_prev_pl_raw,
)

FIXED_TS = "2026-07-07T09:00:00+09:00"


def _progress_by_code():
    prev_pl = compute_pl(sample_prev_pl_raw(), computed_at=FIXED_TS)
    prev_bs = compute_bs(
        sample_prev_bs_raw(), pl_context=pl_context_from_pl(prev_pl), computed_at=FIXED_TS
    )
    curr_pl = compute_pl(sample_pl_raw(), computed_at=FIXED_TS)
    curr_bs = compute_bs(
        sample_bs_raw(), pl_context=pl_context_from_pl(curr_pl), computed_at=FIXED_TS
    )
    prev = {**extract_pl_metrics(prev_pl), **extract_bs_metrics(prev_bs)}
    curr = {**extract_pl_metrics(curr_pl), **extract_bs_metrics(curr_bs)}
    return {p["code"]: p for p in compute_metric_progress(prev, curr)}


def test_higher_better_metrics_direction():
    prog = _progress_by_code()
    assert prog["OPM"]["direction"] == "improved"  # 10.0 → 14.2
    assert prog["OP"]["direction"] == "improved"
    assert prog["cash_runway_months"]["direction"] == "improved"  # 2.0 → 2.7


def test_lower_better_metrics_direction():
    prog = _progress_by_code()
    assert prog["DR"]["direction"] == "improved"  # 137.5 → 122.1 (하락=개선)
    assert prog["ar_days"]["direction"] == "improved"  # 42 → 33


def test_flat_metric_direction():
    prog = _progress_by_code()
    assert prog["GPM"]["direction"] == "flat"  # 35.0 → 35.0


def test_pct_vs_value_keys():
    prog = _progress_by_code()
    assert "value_pct" in prog["OPM"] and "prev_value_pct" in prog["OPM"]
    assert "value" in prog["ar_days"] and "prev_value" in prog["ar_days"]


def test_polarity_direct_unit():
    """극성 딕셔너리 직접 검증: 반대 방향 이동은 worsened."""
    prev = {"OPM": 12.0, "ar_days": 40.0, "DR": 130.0}
    curr = {"OPM": 8.0, "ar_days": 45.0, "DR": 120.0}
    prog = {p["code"]: p["direction"] for p in compute_metric_progress(prev, curr)}
    assert prog["OPM"] == "worsened"  # 하락(higher_better)
    assert prog["ar_days"] == "worsened"  # 상승(lower_better)
    assert prog["DR"] == "improved"  # 하락(lower_better)


def test_untracked_metric_skipped():
    """POLARITY에 없는 코드 또는 한쪽에만 있는 코드는 제외."""
    prog = compute_metric_progress({"UNKNOWN": 1.0, "OPM": 10.0}, {"UNKNOWN": 2.0})
    assert prog == []
