"""데모 모드(DEMO_MODE) 배선 — API 키 없이 데모 데이터로 조종석·리포트를 끝까지 구동.

`ANTHROPIC_API_KEY` 없이도 사용자가 파싱→승인→발행→모바일 리포트를 직접 클릭해 볼 수 있도록
**결정론적 canned 에이전트**(실제 LLM 미호출)를 제공한다. `DEMO_MODE=1` 일 때 `create_app` 이
이 모듈의 `make_demo_orchestrator` / `demo_parse_fn` 을 기본 주입한다.

**주의:** 데모 전용이다. 운영에서는 절대 켜지 말 것(실제 분석이 아니라 고정 예시를 반환한다).
숫자는 실제 `compute/ingest.sample_pl_raw/bs_raw` 의 compute 결과(bep.daily_target_qty=35,
cash_runway_months=2.7)와 정합하도록 맞춰 두었다.
"""

from __future__ import annotations

import os
from typing import Any

from orchestrator import Orchestrator


def is_demo_mode() -> bool:
    """DEMO_MODE 환경변수가 활성인지."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


# --- 결정론적 canned 에이전트(스키마 유효, 실제 Claude 미호출) ---
def _demo_pl(**_kwargs: Any) -> dict[str, Any]:
    return {"agent": "pl_analyst", "summary": "데모: 주력상품 마진 양호, 하루 35마리가 손익분기."}


def _demo_bs(**_kwargs: Any) -> dict[str, Any]:
    return {"agent": "bs_analyst", "summary": "데모: 현금 생존 2.7개월 — 세금 유보 권고."}


def _demo_report(**_kwargs: Any) -> dict[str, Any]:
    return {
        "agent": "report_master",
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": "하루 후라이드 **35마리**면 손익분기입니다. 현금 생존기간(2.7개월)에 유의하세요."},
        ],
        "contradiction_flags": [
            {"between": ["pl_analyst", "bs_analyst"],
             "text": "PL: 마케팅 투자 확대 권고 vs BS: 현금 유보 필요 — 상충 검토됨.",
             "resolved": True},
        ],
        "cited_values": ["bep.daily_target_qty"],
    }


def _period_of(financials_pl: dict[str, Any] | None) -> str:
    """financials_pl.meta.period → 사람이 읽는 기간 라벨(없으면 기본값)."""
    raw = ((financials_pl or {}).get("meta") or {}).get("period")
    if raw == "2025-Q3":
        return "2025년 3분기"
    return raw or "2025년 3분기"


def _client_name(client_profile: dict[str, Any] | None) -> str:
    return (client_profile or {}).get("trade_name") or "홍길동치킨"


def _demo_final(
    *,
    client_profile: dict[str, Any] | None = None,
    financials_pl: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Agent 4 대체 — 모바일 리포트가 볼만하도록 dashboard_payload 를 보강해 반환."""
    name = _client_name(client_profile)
    period = _period_of(financials_pl)
    return {
        "agent": "final_publisher", "version": 2,
        "sections": [
            {"id": "overview", "title": "종합 요약",
             "body_md": f"{name} 대표님, 하루 35마리면 손익분기입니다."},
        ],
        "applied_feedback": [
            {"from": "전문가", "directive_ref": "overall_note",
             "how_applied": "톤을 부드럽게, 현금 유보를 강조", "scope": "global"},
        ],
        "dashboard_payload": {
            "client": {"name": name, "period": period},
            "hero_kpis": [
                {"key": "bep_attainment", "label": "손익분기점 달성률", "value_pct": 268.0},
                {"key": "daily_target_qty", "label": "일일 목표 판매수량", "value": 35, "unit": "개"},
            ],
            "kpis": [
                {"key": "cash_runway_months", "label": "가용현금 생존", "value": 2.7, "unit": "개월", "flag": "warn"},
                {"key": "OPM", "label": "영업이익률", "value_pct": 14.2, "trend": "flat"},
            ],
            "followup": {"prev_period": None, "items": []},
            "charts": [],
            "report_sections": [
                {"id": "profit", "title": "수익성 분석",
                 "body_md": ("주력상품 **후라이드치킨**의 공헌이익이 가장 높습니다. "
                             "손익분기 달성을 위해 **하루 35마리** 판매가 목표입니다.")},
                {"id": "cash", "title": "현금흐름·부채",
                 "body_md": ("가용현금으로 약 **2.7개월** 버틸 수 있습니다. "
                             "다가오는 부가세 납부에 대비해 **세금 유보 계좌**를 분리하세요.")},
                {"id": "master", "title": "마스터 종합 의견",
                 "body_md": ("> 매출은 손익분기를 넉넉히 넘겼지만 현금 방어가 관건입니다. "
                             "이익의 일부를 세금·비상 자금으로 먼저 떼어 두시길 권합니다.")},
            ],
        },
        "recommendations": [
            {"rec_code": "R-2025Q3-01", "text": "세금 유보 계좌 분리",
             "target_metric": "cash_runway_months", "direction": "increase"},
        ],
    }


def demo_parse_fn(
    raw_text: str, *, client_id: str, period: str, approved_memory: Any | None = None,
) -> dict[str, Any]:
    """Agent 0 대체 — 원시 텍스트와 무관하게 유효한 parser_output(대사 일치)을 반환한다."""
    sales = [{"item_name": "후라이드치킨", "selling_price": 18000, "unit_cost": 7200, "quantity": 3900}]
    rev = 18000 * 3900
    return {
        "info": {"client_id": client_id, "period": period, "business_days": 78, "total_revenue": rev},
        "sales": sales,
        "cogs": [{"material_category": "생닭", "amount": 41000000, "prev_amount": 33000000}],
        "opex": [{"account_name": "임차료", "amount": 6000000, "prev_amount": 6000000}],
        "wc": {"ar_days": 33.0, "ap_days": 28.0},
        "inv": [{"category": "포장재", "amount": 4500000, "days_in_inventory": 41.0}],
        "debt": [{"lender": "○○은행", "amount": 60000000, "interest_rate": 5.2, "monthly_payment": 1850000}],
        "od": {"suspense_receipts": 12000000, "suspense_payments": 3500000},
        "bs": {"total_cash": 24000000, "total_ca": 95000000, "total_cl": 62000000, "total_equity": 86000000},
        "schema_extensions": [{"target_sheet": "OPEX", "generated_key": "government_subsidy",
                               "korean_name": "손실보전금", "value": 3000000}],
        "raw_total_check": {"source_raw_sum": rev},
    }


def make_demo_orchestrator(store: Any) -> Orchestrator:
    """데모용 오케스트레이터 — canned 에이전트로 구성(case_store 없음 → RAG/chromadb 불필요)."""
    return Orchestrator(
        store, pl_fn=_demo_pl, bs_fn=_demo_bs, report_fn=_demo_report,
        publish_fn=_demo_final, case_store=None,
    )


# 데모 고객 프로필에서 create_client 로 넘길 필드(seed_demo.py 와 동일).
_DEMO_PROFILE_FIELDS = (
    "trade_name", "industry", "district_type", "location_raw",
    "owner_gender", "owner_age", "owner_age_band", "risk_appetite",
)


def seed_demo_client(session_factory: Any) -> int | None:
    """고객이 하나도 없을 때만 데모 고객 1건을 시드한다(멱등 — 있으면 None).

    조종석 드롭다운이 비어 있지 않도록 기동 시 호출(DEMO_MODE 게이트). 이미 고객이 있으면
    아무 것도 만들지 않는다(중복 방지).
    """
    from compute.ingest import sample_client_profile
    from web.services import create_client, list_clients

    if list_clients(session_factory):
        return None
    profile = sample_client_profile()
    body: dict[str, Any] = {"name": "김사장(데모)"}
    body.update({k: profile[k] for k in _DEMO_PROFILE_FIELDS if k in profile})
    return create_client(session_factory, body)
