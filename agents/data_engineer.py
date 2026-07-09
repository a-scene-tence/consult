"""Agent 0 — 데이터 엔지니어 (Phase 9, P5 자동 표준화 보조).

비표준 소상공인 원시 데이터(POS 영수증 타임라인·배달 정산 텍스트·세금계산서)를 [9대 표준 관리회계
스키마]로 파싱한다. **의미론적 매핑·정규화·동적 스키마 확장만** 수행하고 **사칙연산은 하지 않는다**
(Strict Rule §0.1) — 집계·총합 대사는 `compute/reconcile.py` 가 Python 으로 확정한다.

출력은 `parser_output` 스키마로 검증하며, 위반 시 `self_healing_call` 이 실패 원인을 주입해 자가치유
재시도한다. `numeric_guard`(입력 밖 숫자 금지)는 **적용하지 않는다** — 이 에이전트의 본질이 원시
텍스트에서 숫자를 추출하는 것이기 때문. 대신 reconciliation(리컨실러)이 환각 방어선이다.

이 에이전트의 출력은 **표준화 Master 초안**일 뿐이며, 전문가가 검토·수정·승인한 뒤에만 compute 로
유입된다(§0.5 전문가 게이트 보존).
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import self_healing_call
from compute._common import load_schema, validate_payload

AGENT_NAME = "data_engineer"
_TOOL_NAME = "emit_parser_output"
_SCHEMA_NAME = "parser_output"

# 9대 표준 스키마 가이드(파싱 대상 구조 안내).
_SCHEMA_GUIDE = """[9대 표준 관리회계 스키마 가이드]
1. info: {client_id, period, business_days, total_revenue(원시 보고 매출총액)}
2. sales[]: {item_name(정규화), selling_price, unit_cost, quantity}
3. cogs[]: {material_category, amount, prev_amount(있으면)}
4. opex[]: {account_name(표준 계정명으로 매핑), amount, prev_amount(있으면)}
5. wc: {ar_days, ap_days}
6. inv[]: {category, amount, days_in_inventory}
7. debt[]: {lender, amount, interest_rate, monthly_payment}
8. od(오너 자금 혼용): {suspense_receipts(가수금), suspense_payments(가지급금)}
9. bs: {total_cash, total_ca, total_cl, total_equity}
그 외 표준에 매핑되지 않는 비즈니스 크리티컬 데이터는 schema_extensions[] 에 격리(누락 금지).
raw_total_check.source_raw_sum 에는 원시(가공 전) 매출/매입 총액을 그대로 담아 대사에 쓴다."""

# 사용자 제시 시스템 프롬프트(3대 규칙: 의미론적 매핑·동적 스키마 확장·대사).
_SYSTEM = (
    "당신은 비표준화된 소상공인 원시 데이터(POS 영수증 타임라인, 배달 정산 내역 텍스트, 국세청 매입 "
    "세금계산서)를 파싱하여, 시스템의 [9대 표준 관리회계 스키마]로 변환하는 오차 제로(0%)의 수석 데이터 "
    "엔지니어이자 관리회계 전문가입니다. 다음 3대 규칙을 엄격히 준수해 구조화된 JSON 만 반환합니다.\n\n"
    "### 1. 의미론적 매핑 및 정규화\n"
    "- 중구난방 텍스트 명칭을 표준 식별자로 변환하라(예: '후라이드치킨(반반) 무추가'→item_name '후라이드치킨'; "
    "'건물주 이순신 월세 입금'→account_name '임차료').\n"
    "- 수치에서 쉼표·'원'·'개'·'%' 등 문자를 완벽히 제거해 순수 숫자(Integer/Float)로만 추출하라.\n\n"
    "### 2. 동적 스키마 확장\n"
    "- 9대 표준에 명확히 매핑되지 않는 비즈니스 크리티컬 데이터는 절대 누락하지 말고, 고유한 snake_case "
    "영어 key 를 스스로 생성해 schema_extensions 에 격리 보존하라(예: '소상공인 손실보전금 3,000,000원'→"
    "generated_key 'government_subsidy').\n\n"
    "### 3. 데이터 대사 및 오차 검토\n"
    "- **수치 환각 방지를 위해 LLM 수준에서 무리한 사칙연산을 직접 수행하지 말고**, 추출된 원시 로우 데이터 "
    "배열을 온전히 전달하라. 정확한 계산은 파이썬 코드가 수행한다. raw_total_check.source_raw_sum 에는 "
    "원시 총합의 원천 수치를 담아 대사가 가능하게 하라.\n\n"
    + _SCHEMA_GUIDE
)


def _build_user_message(raw_text: str, client_id: str, period: str) -> str:
    return "\n".join([
        f"[대상] client_id={client_id}, period={period}",
        "",
        "[비표준 로데이터] — 아래 원시 데이터를 9대 표준 스키마로 파싱하라:",
        raw_text,
        "",
        "반드시 제공된 도구를 호출해 parser_output JSON 스키마로만 응답하라. info.client_id/period 는 위 값을 사용하라.",
    ])


def parse_raw(
    raw_text: str,
    *,
    client_id: str,
    period: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """원시 데이터 텍스트를 parser_output(표준화 Master 초안)으로 파싱한다.

    Raises:
        ValueError: 자가치유 재시도 후에도 출력이 parser_output 스키마를 위반할 때.
    """
    user = _build_user_message(raw_text, client_id, period)

    def _validate(output: dict[str, Any]) -> None:
        validate_payload(output, _SCHEMA_NAME)  # 스키마 위반 → ValueError (자가치유 재시도)

    return self_healing_call(
        system=_SYSTEM,
        user=user,
        tool_name=_TOOL_NAME,
        tool_description="비표준 원시 데이터를 9대 표준 스키마로 파싱한 결과를 제출한다.",
        input_schema=load_schema(_SCHEMA_NAME),
        validate=_validate,
        client=client,
    )


# 스모크/테스트용 원시 데이터 픽스처(POS·배달·세금계산서 혼합 모사).
def sample_raw_text() -> str:
    return json.dumps(
        {
            "pos_timeline": [
                "07/01 후라이드치킨(반반) 무추가 18,000원 x 3건",
                "07/01 콜라(병) 2,000원 x 5건",
            ],
            "delivery_settlement": "배달의민족 정산 배달수수료 8,200,000원 차감",
            "tax_invoices": ["생닭 매입 41,000,000원", "식용유 12,500,000원"],
            "notes": "소상공인 손실보전금 정부지원금 3,000,000원 입금",
        },
        ensure_ascii=False,
    )
