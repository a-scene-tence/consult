#!/usr/bin/env python
"""실전 통합 시뮬레이션 (Phase 6) — 실제 Anthropic·PostgreSQL·ChromaDB 로 워크플로 중계.

Fake/InMemory 가 아닌 **실 인프라**를 물고 0단계(고객 등록)부터 발행·대시보드까지의 전 과정을
콘솔에 텍스트로 중계한다. 개발자가 실제 LLM 응답 품질과 `numeric_guard` 작동을 눈으로 확인하는 용도.

사용법:
    # .env 에 ANTHROPIC_API_KEY, DATABASE_URL(실 PostgreSQL) 설정
    #   (선택) RAG_ENABLED=1 + CHROMA_PERSIST_DIR 로 실 ChromaDB 적재까지 확인
    python scripts/run_real_e2e.py

주의:
    - 실제 Claude(claude-opus-4-8) 를 4회 이상 호출한다(과금·지연 발생).
    - 테이블이 없으면 생성한다(create_all). 운영은 `alembic upgrade head` 권장.
    - 이 스크립트는 오케스트레이터를 **동기(inline)** 로 직접 구동한다(Celery 큐 미경유 — 단계별 중계 목적).
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Any

# 프로젝트 루트를 path 에 추가(스크립트 단독 실행 대비).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv 미설치 시에도 환경변수로 동작
    pass

# 이 스크립트는 단계 중계를 위해 항상 inline 실행.
os.environ.setdefault("TASK_BACKEND", "inline")


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def section(title: str) -> None:
    print("\n" + _c("1;36", f"━━━ {title} " + "━" * max(0, 60 - len(title))))


def show(label: str, value: Any) -> None:
    import json

    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    print(f"{_c('1;33', label)}: {value}")


def _require_env() -> None:
    missing = [k for k in ("ANTHROPIC_API_KEY", "DATABASE_URL") if not os.environ.get(k)]
    if missing:
        print(_c("1;31", f"환경변수 누락: {missing}. .env 를 설정하세요."))
        sys.exit(1)


def main() -> None:
    _require_env()

    from compute.ingest import sample_bs_raw, sample_client_profile, sample_pl_raw
    from db.session import create_all, make_engine, make_session_factory
    from guards.numeric_guard import NumericGuardViolation
    from orchestrator import SqlAlchemyStore
    from rag.chroma_client import InMemoryCaseStore
    from web.production import prod_case_store
    from web.services import create_client, get_dashboard, ingest_financials, record_feedback

    section("0. 인프라 준비")
    engine = make_engine()
    create_all(engine)  # 운영은 alembic upgrade head 권장
    session_factory = make_session_factory(engine)
    case_store = prod_case_store() or InMemoryCaseStore()
    show("DB", os.environ["DATABASE_URL"].split("@")[-1])
    show("CaseStore", type(case_store).__name__)
    show("LLM", os.environ.get("LLM_MODEL", "claude-opus-4-8"))

    # 실 Agent 오케스트레이터(기본 pl/bs/report/final = analyze_* 실 호출).
    from orchestrator import Orchestrator

    store = SqlAlchemyStore(session_factory)
    from rag.case_indexer import index_published_case

    orch = Orchestrator(store, case_store=case_store, index_fn=index_published_case)

    section("1. 고객(CRM) 등록")
    profile = sample_client_profile()
    client_id = create_client(session_factory, {
        "name": "김사장", **{k: profile[k] for k in (
            "trade_name", "industry", "district_type", "location_raw",
            "owner_gender", "owner_age", "owner_age_band", "risk_appetite")},
    })
    show("client_id", client_id)

    section("2. Master 업로드 → compute 확정 수치")
    draft_id = ingest_financials(
        session_factory, client_id, "2025-Q3", sample_pl_raw(), sample_bs_raw())
    inputs = store.load_inputs(draft_id)
    show("draft_id", draft_id)
    show("BEP(일일목표/달성률)", {
        "daily_target_qty": inputs["financials_pl"]["bep"]["daily_target_qty"],
        "bep_attainment_pct": inputs["financials_pl"]["bep"]["bep_attainment_pct"],
    })
    show("현금흐름(CCC/DSCR/Runway)", inputs["financials_bs"]["cash_flow"])

    section("3. Agent 1~3 실행 (실 Claude 호출 + numeric_guard)")
    try:
        a3 = orch.run_analysis(draft_id)
    except NumericGuardViolation as exc:
        show(_c("1;31", "numeric_guard 위반(환각 차단)"), str(exc))
        print(_c("1;31", "→ 에이전트가 확정 수치 밖의 숫자를 생성해 차단됨. 상태=failed."))
        return
    show("초안 상태", store.get_draft(draft_id)["status"])
    show("종합 초안 섹션 수", len(a3.get("sections", [])))
    show("상호 모순 플래그", a3.get("contradiction_flags", []))
    for s in a3.get("sections", [])[:2]:
        show(f"  [{s['title']}]", s["body_md"][:300])

    section("4. 전문가 피드백 → Agent 4 재작성")
    feedback = {"reviewer": "세무 파트너",
                "overall_note": "사장님이 겁먹지 않게 톤을 부드럽게, 현금 유보를 최우선으로 강조.",
                "instructions": []}
    record_feedback(session_factory, draft_id, feedback)
    try:
        a4 = orch.submit_feedback(draft_id, feedback)
    except NumericGuardViolation as exc:
        show(_c("1;31", "numeric_guard 위반(환각 차단)"), str(exc))
        return
    show("applied_feedback", a4.get("applied_feedback", []))
    show("dashboard hero_kpis", a4["dashboard_payload"]["hero_kpis"])
    show("recommendations", a4.get("recommendations", []))

    section("5. 승인·발행 (+ RAG 마스킹·적재)")
    orch.approve(draft_id)
    orch.publish(draft_id)
    show("최종 상태", store.get_draft(draft_id)["status"])

    section("6. 고객 대시보드 조회")
    dash = get_dashboard(session_factory, client_id)
    show("hero_kpis", dash["hero_kpis"] if dash else None)
    show("kpis", dash["kpis"] if dash else None)

    print("\n" + _c("1;32", "✅ 실전 통합 워크플로 완료 — 실 LLM·DB·(옵션)ChromaDB 연동 확인."))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # 어떤 단계에서 끊겼는지 개발자에게 그대로 노출
        traceback.print_exc()
        sys.exit(1)
