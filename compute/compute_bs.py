"""compute_bs — BS(재무상태표) 확정 수치 계산.

CLAUDE.md §0-1(Strict Rule): 재무 산술이 확정되는 유일 지점 중 하나.
입력(raw 기초 데이터) → Pandas 연산 → `financials.bs` JSON(스키마 검증 통과) 반환.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from compute._common import round_pct, to_money, validate_payload

# 최종 출력 계정 순서.
_ACCOUNT_ORDER = ["CA", "CL", "INV", "AR", "AP", "DEBT", "EQUITY"]
_REQUIRED_BASE = ("CA", "CL", "DEBT", "EQUITY")


def compute_bs(raw: dict[str, Any], *, computed_at: str | None = None) -> dict[str, Any]:
    """raw 기초 데이터로부터 financials.bs 확정 JSON을 산출한다.

    지표: CR(유동비율) = CA/CL*100, WC(운전자본) = CA - CL, DR(부채비율) = DEBT/EQUITY*100.
    """
    meta_in = raw["meta"]
    base = raw["accounts"]
    for code in _REQUIRED_BASE:
        if code not in base:
            raise ValueError(f"BS raw 데이터에 필수 계정 '{code}' 이(가) 없습니다.")

    # 금액 벡터 (Pandas). 출력은 raw 에 존재하는 계정만, 지정 순서로.
    amount = pd.Series({c: base[c]["amount"] for c in base})
    order = [c for c in _ACCOUNT_ORDER if c in base]
    acc_df = pd.DataFrame(
        {
            "code": order,
            "name": [base[c]["name"] for c in order],
            "amount": [amount[c] for c in order],
        }
    )
    accounts = [
        {"code": row.code, "name": row.name, "amount": to_money(row.amount)}
        for row in acc_df.itertuples(index=False)
    ]

    if amount["CL"] == 0:
        raise ValueError("유동부채(CL)가 0이라 유동비율(CR)을 계산할 수 없습니다.")
    if amount["EQUITY"] == 0:
        raise ValueError("자본총계(EQUITY)가 0이라 부채비율(DR)을 계산할 수 없습니다.")

    ratios = [
        {
            "code": "CR",
            "name": "유동비율",
            "value_pct": round_pct(amount["CA"] / amount["CL"] * 100),
        },
        {
            "code": "WC",
            "name": "운전자본",
            "value": to_money(amount["CA"] - amount["CL"]),
        },
        {
            "code": "DR",
            "name": "부채비율",
            "value_pct": round_pct(amount["DEBT"] / amount["EQUITY"] * 100),
        },
    ]

    payload = {
        "meta": {
            "client_id": meta_in["client_id"],
            "period": meta_in["period"],
            "currency": meta_in["currency"],
            "computed_at": computed_at or datetime.now(timezone.utc).isoformat(),
        },
        "accounts": accounts,
        "ratios": ratios,
    }
    return validate_payload(payload, "financials_bs")
