"""데모 데이터 시드 — 발행 완료된 데모 고객 1건을 즉시 조회 가능하게 프리로드한다.

API 키 없이(결정론적 canned 에이전트) 고객 등록 → Master compute → 분석 → 피드백 → 승인 → 발행
전 과정을 실행하고, 조종석 접속 정보와 **사장님 모바일 리포트 공유 링크**를 출력한다.

사용:  DATABASE_URL="sqlite:///./demo.db" python scripts/seed_demo.py

주의: 매 실행마다 새 데모 고객을 생성한다(누적). 깨끗이 보려면 sqlite 파일을 지우고 다시 실행하라.
운영 DB 에는 실행하지 말 것 — 데모 전용 고정 데이터가 들어간다.
"""

from __future__ import annotations

import os
import sys

# 스크립트를 저장소 루트에서 실행하지 않아도 임포트되도록 경로 보정.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
from db.session import create_all, make_engine, make_session_factory
from orchestrator import SqlAlchemyStore
from web.auth import seed_default_users
from web.demo import make_demo_orchestrator
from web.services import create_client, ingest_financials, list_clients

_DEMO_OWNER_NAME = "김사장(데모)"
_PERIOD = "2025-Q3"
_PROFILE_FIELDS = (
    "trade_name", "industry", "district_type", "location_raw",
    "owner_gender", "owner_age", "owner_age_band", "risk_appetite",
)


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("[오류] DATABASE_URL 이 설정되지 않았습니다. 예:")
        print('       export DATABASE_URL="sqlite:///./demo.db"')
        return 1

    engine = make_engine()
    create_all(engine)  # 테이블 보장(멱등)
    sf = make_session_factory(engine)
    seed_default_users(sf)  # admin / owner 데모 계정

    profile = sample_client_profile()
    body = {"name": _DEMO_OWNER_NAME}
    body.update({k: profile[k] for k in _PROFILE_FIELDS if k in profile})

    client_id = create_client(sf, body)
    draft_id = ingest_financials(sf, client_id, _PERIOD, sample_pl_raw(), sample_bs_raw())

    orch = make_demo_orchestrator(SqlAlchemyStore(sf))
    orch.run_analysis(draft_id)  # computed → review_pending (canned Agent 1·2·3)
    orch.submit_feedback(draft_id, {
        "reviewer": "세무 파트너", "overall_note": "사장님 눈높이로 톤 부드럽게", "instructions": [],
    })  # → version 2 (canned Agent 4)
    orch.approve(draft_id)
    payload = orch.publish(draft_id)  # → published + dashboard_payload

    token = next((c["report_token"] for c in list_clients(sf) if c["id"] == client_id), None)
    hero = ", ".join(f"{k['label']}={k.get('value_pct', k.get('value'))}"
                     for k in payload["dashboard_payload"]["hero_kpis"])

    print("\n" + "=" * 64)
    print("  데모 시드 완료 ✅")
    print("=" * 64)
    print(f"  고객: {body['trade_name']} (client_id={client_id}, draft_id={draft_id})")
    print(f"  Hero KPI: {hero}")
    print(f"  로그인: admin / admin-secret")
    print(f"  조종석:      http://localhost:8000/static/index.html")
    print(f"  사장님 리포트: http://localhost:8000/static/client_report.html?token={token}")
    print("=" * 64)
    print("  (원격/Codespaces 는 localhost 대신 전달된 8000 포트 URL 로 접속하세요.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
