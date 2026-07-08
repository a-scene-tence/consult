"""raw 업로드 → 정형화. (v0.3: 전문가 정제 Master 엑셀/CSV를 모사한 딕셔너리)

P5 게이트: 파이프라인은 전문가가 교차 대조·정제한 표준화 Master 양식만 수용한다.
여기서는 compute_pl/compute_bs 의 입력 계약을 정의하고, 테스트/스모크용 샘플 픽스처를 제공한다.
raw 에는 파생값(마진·BEP·CCC 등)을 넣지 않는다 — 파생·비율 계산은 compute_* 의 책임이다.

Follow-up(시계열) 테스트를 위해 직전 회차(2025-Q2) 픽스처도 함께 제공한다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def accounts_to_frame(accounts: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """{code: {...}} 형태 계정 딕셔너리를 DataFrame으로 정형화(index=code)."""
    frame = pd.DataFrame.from_dict(accounts, orient="index")
    frame.index.name = "code"
    return frame


# ---------------------------------------------------------------------------
# 당기(2025-Q3) 샘플
# ---------------------------------------------------------------------------
def sample_pl_raw() -> dict[str, Any]:
    """PL 연산용 샘플 Master raw (당기 2025-Q3)."""
    return {
        "meta": {
            "client_id": "C-1001",
            "period": "2025-Q3",
            "prev_period": "2025-Q2",
            "currency": "KRW",
            "business_days": 78,
        },
        "accounts": {
            "REV": {"name": "매출액", "amount": 130_000_000, "prev_amount": 120_000_000},
            "COGS": {"name": "매출원가", "amount": 84_500_000, "prev_amount": 78_000_000},
            "OPEX": {"name": "판매관리비", "amount": 27_000_000, "prev_amount": 25_000_000},
        },
        "unallocated_cash_sales": {"amount": 9_100_000},
        "sales_details": [
            {"item_name": "후라이드치킨", "selling_price": 18_000, "unit_cost": 7_200, "quantity": 3_900},
            {"item_name": "양념치킨", "selling_price": 19_000, "unit_cost": 8_600, "quantity": 2_100},
            {"item_name": "콜라(병)", "selling_price": 2_000, "unit_cost": 1_400, "quantity": 5_200},
        ],
        "cogs_details": [
            {"material_category": "생닭", "amount": 41_000_000, "prev_amount": 33_000_000},
            {"material_category": "식용유", "amount": 12_500_000, "prev_amount": 11_800_000},
        ],
        "opex_details": [
            {"account_name": "배달수수료", "amount": 8_200_000, "prev_amount": 6_800_000},
            {"account_name": "임차료", "amount": 6_000_000, "prev_amount": 6_000_000},
            {"account_name": "감가상각비", "amount": 2_000_000, "prev_amount": 1_800_000},
        ],
        "milestones": [],
    }


def sample_bs_raw() -> dict[str, Any]:
    """BS 연산용 샘플 Master raw (당기 2025-Q3)."""
    return {
        "meta": {"client_id": "C-1001", "period": "2025-Q3", "currency": "KRW"},
        "accounts": {
            "CASH": {"name": "현금및예금", "amount": 24_000_000},
            "CA": {"name": "유동자산", "amount": 95_000_000},
            "CL": {"name": "유동부채", "amount": 62_000_000},
            "DEBT": {"name": "총부채", "amount": 105_000_000},
            "EQUITY": {"name": "자본총계", "amount": 86_000_000},
        },
        "inventory_details": [
            {"category": "생닭(냉장)", "amount": 9_000_000, "days_in_inventory": 6.0},
            {"category": "포장재", "amount": 4_500_000, "days_in_inventory": 41.0},
        ],
        "debt_details": [
            {"lender": "○○은행 운전자금대출", "amount": 60_000_000, "interest_rate": 5.2, "monthly_payment": 1_850_000},
            {"lender": "△△캐피탈", "amount": 25_000_000, "interest_rate": 9.8, "monthly_payment": 1_230_000},
        ],
        "working_capital_details": {"ar_days": 33.0, "ap_days": 28.0},
        "owner_draws": {"suspense_receipts": 12_000_000, "suspense_payments": 3_500_000},
    }


# ---------------------------------------------------------------------------
# 직전 회차(2025-Q2) 샘플 — Follow-up(metric_progress) 검증용
# ---------------------------------------------------------------------------
def sample_prev_pl_raw() -> dict[str, Any]:
    """직전 회차 PL (2025-Q2). 당기 대비 OPM 낮음(개선 전)."""
    return {
        "meta": {
            "client_id": "C-1001",
            "period": "2025-Q2",
            "prev_period": "2025-Q1",
            "currency": "KRW",
            "business_days": 76,
        },
        "accounts": {
            "REV": {"name": "매출액", "amount": 120_000_000, "prev_amount": 100_000_000},
            "COGS": {"name": "매출원가", "amount": 78_000_000, "prev_amount": 70_000_000},
            "OPEX": {"name": "판매관리비", "amount": 30_000_000, "prev_amount": 28_000_000},
        },
        "unallocated_cash_sales": {"amount": 12_000_000},
        "sales_details": [
            {"item_name": "후라이드치킨", "selling_price": 18_000, "unit_cost": 7_200, "quantity": 3_600},
            {"item_name": "양념치킨", "selling_price": 19_000, "unit_cost": 8_600, "quantity": 2_000},
            {"item_name": "콜라(병)", "selling_price": 2_000, "unit_cost": 1_400, "quantity": 5_000},
        ],
        "cogs_details": [
            {"material_category": "생닭", "amount": 33_000_000, "prev_amount": 30_000_000},
        ],
        "opex_details": [
            {"account_name": "감가상각비", "amount": 1_800_000, "prev_amount": 1_700_000},
        ],
        "milestones": [],
    }


def sample_prev_bs_raw() -> dict[str, Any]:
    """직전 회차 BS (2025-Q2). 당기 대비 DR·ar_days 높음(개선 전)."""
    return {
        "meta": {"client_id": "C-1001", "period": "2025-Q2", "currency": "KRW"},
        "accounts": {
            "CASH": {"name": "현금및예금", "amount": 20_000_000},
            "CA": {"name": "유동자산", "amount": 90_000_000},
            "CL": {"name": "유동부채", "amount": 60_000_000},
            "DEBT": {"name": "총부채", "amount": 110_000_000},
            "EQUITY": {"name": "자본총계", "amount": 80_000_000},
        },
        "inventory_details": [
            {"category": "생닭(냉장)", "amount": 8_000_000, "days_in_inventory": 7.0},
        ],
        "debt_details": [
            {"lender": "○○은행 운전자금대출", "amount": 65_000_000, "interest_rate": 5.2, "monthly_payment": 1_950_000},
        ],
        "working_capital_details": {"ar_days": 42.0, "ap_days": 26.0},
        "owner_draws": {"suspense_receipts": 15_000_000, "suspense_payments": 2_000_000},
    }
