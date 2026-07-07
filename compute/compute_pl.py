"""compute_pl — PL(손익계산서) 확정 수치 계산.

CLAUDE.md §0-1(Strict Rule): 재무 산술이 확정되는 유일 지점 중 하나.
입력(raw 기초 데이터) → Pandas 연산 → `financials.pl` JSON(스키마 검증 통과) 반환.
결정론적: 동일 입력이면 동일 출력(computed_at 제외).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from compute._common import round_pct, to_money, validate_payload

# 파생 계정의 표시명.
_DERIVED_NAMES = {"GP": "매출총이익", "OP": "영업이익"}
# 최종 출력 계정 순서.
_ACCOUNT_ORDER = ["REV", "COGS", "GP", "OPEX", "OP"]
_REQUIRED_BASE = ("REV", "COGS", "OPEX")


def compute_pl(raw: dict[str, Any], *, computed_at: str | None = None) -> dict[str, Any]:
    """raw 기초 데이터로부터 financials.pl 확정 JSON을 산출한다.

    파생 계정: GP = REV - COGS, OP = GP - OPEX (당기/전기 각각).
    비율: GPM = GP/REV*100, OPM = OP/REV*100 (당기/전기).
    마일스톤: variance_pct = (actual - planned)/planned*100.
    """
    meta_in = raw["meta"]
    base = raw["accounts"]
    for code in _REQUIRED_BASE:
        if code not in base:
            raise ValueError(f"PL raw 데이터에 필수 계정 '{code}' 이(가) 없습니다.")

    # 당기/전기 금액 벡터 (Pandas)
    amount = pd.Series({c: base[c]["amount"] for c in base})
    prev = pd.Series({c: base[c]["prev_amount"] for c in base})

    # 파생 계정 계산
    amount["GP"] = amount["REV"] - amount["COGS"]
    prev["GP"] = prev["REV"] - prev["COGS"]
    amount["OP"] = amount["GP"] - amount["OPEX"]
    prev["OP"] = prev["GP"] - prev["OPEX"]

    names = {c: base[c]["name"] for c in base} | _DERIVED_NAMES

    # 계정 프레임 구성 후 전기대비 증감률을 Pandas로 벡터 계산
    acc_df = pd.DataFrame(
        {
            "code": _ACCOUNT_ORDER,
            "name": [names[c] for c in _ACCOUNT_ORDER],
            "amount": [amount[c] for c in _ACCOUNT_ORDER],
            "prev_amount": [prev[c] for c in _ACCOUNT_ORDER],
        }
    )
    if (acc_df["prev_amount"] == 0).any():
        raise ValueError("전기 금액이 0인 계정이 있어 yoy_pct 를 계산할 수 없습니다.")
    acc_df["yoy_pct"] = (
        (acc_df["amount"] - acc_df["prev_amount"]) / acc_df["prev_amount"] * 100
    ).round(1)

    accounts = [
        {
            "code": row.code,
            "name": row.name,
            "amount": to_money(row.amount),
            "prev_amount": to_money(row.prev_amount),
            "yoy_pct": float(row.yoy_pct),
        }
        for row in acc_df.itertuples(index=False)
    ]

    # 수익성 비율 (당기/전기)
    ratios = [
        {
            "code": "GPM",
            "name": "매출총이익률",
            "value_pct": round_pct(amount["GP"] / amount["REV"] * 100),
            "prev_value_pct": round_pct(prev["GP"] / prev["REV"] * 100),
        },
        {
            "code": "OPM",
            "name": "영업이익률",
            "value_pct": round_pct(amount["OP"] / amount["REV"] * 100),
            "prev_value_pct": round_pct(prev["OP"] / prev["REV"] * 100),
        },
    ]

    # 마일스톤 성과
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
            "prev_period": meta_in["prev_period"],
            "currency": meta_in["currency"],
            "computed_at": computed_at or datetime.now(timezone.utc).isoformat(),
        },
        "accounts": accounts,
        "ratios": ratios,
        "milestones": milestones,
    }
    return validate_payload(payload, "financials_pl")
