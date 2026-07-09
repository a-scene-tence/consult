"""Agent 0(데이터 엔지니어) 테스트 (Phase 9) — API 모킹.

원시 데이터 파싱 결과가 parser_output 스키마를 통과하는지, 스키마 위반 시 자가치유 재시도 후
ValueError 로 실패하는지 검증한다. numeric_guard 는 적용하지 않는다(추출 에이전트).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.data_engineer import parse_raw


class _FakeClient:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        out = self._outputs.pop(0)
        block = SimpleNamespace(type="tool_use", name=kwargs["tools"][0]["name"], input=out)
        return SimpleNamespace(content=[block])


def _valid_output():
    return {
        "info": {"client_id": "C-1001", "period": "2025-Q3", "business_days": 78, "total_revenue": 70200000},
        "sales": [{"item_name": "후라이드치킨", "selling_price": 18000, "unit_cost": 7200, "quantity": 3900}],
        "cogs": [{"material_category": "생닭", "amount": 41000000, "prev_amount": 33000000}],
        "opex": [{"account_name": "임차료", "amount": 6000000}],
        "wc": {"ar_days": 33.0, "ap_days": 28.0},
        "inv": [{"category": "포장재", "amount": 4500000, "days_in_inventory": 41.0}],
        "debt": [{"lender": "○○은행", "amount": 60000000, "interest_rate": 5.2, "monthly_payment": 1850000}],
        "od": {"suspense_receipts": 12000000, "suspense_payments": 3500000},
        "bs": {"total_cash": 24000000, "total_ca": 95000000, "total_cl": 62000000, "total_equity": 86000000},
        "schema_extensions": [
            {"target_sheet": "OPEX", "generated_key": "government_subsidy",
             "korean_name": "소상공인 손실보전금", "value": 3000000}
        ],
        "raw_total_check": {"source_raw_sum": 70200000},
    }


def test_parse_raw_valid():
    out = parse_raw("POS 원시데이터...", client_id="C-1001", period="2025-Q3",
                    client=_FakeClient([_valid_output()]))
    assert out["info"]["client_id"] == "C-1001"
    assert out["schema_extensions"][0]["generated_key"] == "government_subsidy"


def test_parse_raw_self_heals_then_valid():
    """1차 스키마 위반(필수 raw_total_check 누락) → 2차 교정 정상 → 성공."""
    bad = _valid_output()
    del bad["raw_total_check"]
    client = _FakeClient([bad, _valid_output()])
    out = parse_raw("...", client_id="C-1001", period="2025-Q3", client=client)
    assert out["raw_total_check"]["source_raw_sum"] == 70200000
    assert client.calls == 2


def test_parse_raw_persistent_schema_violation_raises():
    bad = _valid_output()
    del bad["info"]  # 필수 블록 누락
    with pytest.raises(ValueError):
        parse_raw("...", client_id="C-1001", period="2025-Q3",
                  client=_FakeClient([bad, bad, bad]))
