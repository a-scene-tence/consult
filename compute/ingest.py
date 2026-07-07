"""raw 업로드 → 정형화. (Phase 1: 엑셀/CSV를 모사한 딕셔너리 데이터 사용)

실제 파일 파싱은 후속 Phase에서 추가한다. 여기서는 연산 모듈(compute_pl/compute_bs)의
입력 계약을 정의하고, 테스트/스모크에 쓰일 샘플 픽스처를 제공한다.

raw 입력 계약:
- PL: meta(client_id, period, prev_period, currency) + accounts{REV, COGS, OPEX ...}
      (각 계정은 name/amount/prev_amount) + milestones[]
- BS: meta(client_id, period, currency) + accounts{CA, CL, ... } (각 계정은 name/amount)

주의: raw 에는 파생 수치(GP/OP/비율)를 넣지 않는다. 파생·비율 계산은 compute_* 의 책임이다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def accounts_to_frame(accounts: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """{code: {name, amount, prev_amount?}} 형태의 계정 딕셔너리를 DataFrame으로 정형화.

    index=code, columns=[name, amount, (prev_amount)].
    """
    frame = pd.DataFrame.from_dict(accounts, orient="index")
    frame.index.name = "code"
    return frame


def sample_pl_raw() -> dict[str, Any]:
    """PL 연산용 샘플 raw 데이터 (SPEC §2.6 예시와 동일한 골든 케이스)."""
    return {
        "meta": {
            "client_id": "C-1001",
            "period": "2025-Q2",
            "prev_period": "2025-Q1",
            "currency": "KRW",
        },
        "accounts": {
            "REV": {"name": "매출액", "amount": 120_000_000, "prev_amount": 100_000_000},
            "COGS": {"name": "매출원가", "amount": 78_000_000, "prev_amount": 70_000_000},
            "OPEX": {"name": "판매관리비", "amount": 25_000_000, "prev_amount": 22_000_000},
        },
        "milestones": [
            {"code": "M1", "name": "1차 마일스톤", "planned": 50_000_000, "actual": 52_000_000},
        ],
    }


def sample_bs_raw() -> dict[str, Any]:
    """BS 연산용 샘플 raw 데이터 (SPEC §2.6 예시와 동일한 골든 케이스)."""
    return {
        "meta": {
            "client_id": "C-1001",
            "period": "2025-Q2",
            "currency": "KRW",
        },
        "accounts": {
            "CA": {"name": "유동자산", "amount": 90_000_000},
            "CL": {"name": "유동부채", "amount": 60_000_000},
            "INV": {"name": "재고자산", "amount": 30_000_000},
            "AR": {"name": "매출채권", "amount": 40_000_000},
            "AP": {"name": "매입채무", "amount": 25_000_000},
            "DEBT": {"name": "총부채", "amount": 110_000_000},
            "EQUITY": {"name": "자본총계", "amount": 80_000_000},
        },
    }
