"""지속 학습 적재·조회 파이프라인 (SPEC §1.4·§3.4, CLAUDE.md §2.4·§3.5).

- **적재(역방향):** 발행본 → 마스킹 v2 → `past_case` 레코드 → CaseStore upsert →
  `kb_ingestions` 감사. 멱등성(동일 draft_id 1회), PII 검증 실패 시 중단.
- **조회(정방향):** 현재 고객 코호트 → 완화 Fallback 조회 → 주입 전 PII 재검증(이중 방어) →
  `rag_context` 조립·검증. 임계값 미달·무결과면 None(빈 컨텍스트).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from compute._common import validate_payload
from compute.compute_metrics import extract_bs_metrics, extract_pl_metrics
from db.models import KbIngestion
from rag.chroma_client import CaseStore, retrieve_cases
from rag.embed import case_document
from rag.masking import (
    MASKING_VERSION,
    PiiLeak,
    assert_no_pii,
    build_past_case,
    pct_band,
    ratio_band,
    runway_band,
)

_LESSON_SEP = ""  # 메타 문자열 내 교훈 구분자(사용자 텍스트에 등장하지 않는 제어문자).


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# --------------------------------------------------------------------------
# 적재(역방향)
# --------------------------------------------------------------------------
def _store_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """past_case 레코드를 CaseStore 메타(스칼라만)로 평탄화.

    ChromaDB 메타는 스칼라만 허용하므로 expert_lessons 리스트는 구분자로 join 한다.
    조회 시 `_case_from_metadata` 로 복원한다.
    """
    meta: dict[str, Any] = dict(record["cohort_meta"])
    meta["situation_summary"] = record["situation_summary"]
    meta["expert_lessons"] = _LESSON_SEP.join(record.get("expert_lessons", []))
    meta["outcome_label"] = record["outcome_label"]
    return meta


def index_published_case(
    case_store: CaseStore,
    session_factory: Any,
    draft_id: int,
    *,
    published: dict[str, Any],
    expert_feedback: dict[str, Any] | None,
    client_profile: dict[str, Any],
    financials_pl: dict[str, Any],
    financials_bs: dict[str, Any],
    outcome_label: str = "success",
    now: datetime | None = None,
) -> str | None:
    """발행본을 마스킹해 past_consulting_cases 에 적재하고 kb_ingestions 감사를 남긴다.

    멱등성: 동일 draft_id 가 이미 success 면 재적재하지 않고 기존 case_id 반환. 실패 시
    kb_ingestions 를 failed 로 기록 후 재-raise(오케스트레이터가 비차단 처리). 지수 백오프
    재시도는 프로덕션 워커에서 수행(여기서는 동기·결정론적).
    """
    case_id = f"K-{draft_id:06d}"

    # 멱등성 — 이미 성공 적재면 skip.
    with session_factory() as session:
        existing = (
            session.query(KbIngestion).filter_by(draft_id=draft_id).one_or_none()
        )
        if existing and existing.status == "success":
            return existing.case_id

    try:
        record = build_past_case(
            published, expert_feedback, client_profile, financials_pl, financials_bs,
            case_id=case_id, outcome_label=outcome_label,
        )
        # 적재 전 PII 이중 방어(§3.5 b) — 현재 고객의 상호/위치/나이 잔존 검사.
        assert_no_pii(
            record,
            trade_name=client_profile.get("trade_name"),
            location_raw=client_profile.get("location_raw"),
            owner_age=client_profile.get("owner_age"),
        )
        case_store.upsert(case_id, case_document(record), _store_metadata(record))
        _write_ingestion(session_factory, draft_id, case_id, "success", now or _now())
        return case_id
    except Exception:
        _write_ingestion(session_factory, draft_id, case_id, "failed", None)
        raise


def _write_ingestion(
    session_factory: Any,
    draft_id: int,
    case_id: str | None,
    status: str,
    embedded_at: datetime | None,
) -> None:
    """kb_ingestions 감사 행을 upsert(멱등 draft_id)."""
    with session_factory() as session:
        row = session.query(KbIngestion).filter_by(draft_id=draft_id).one_or_none()
        if row is None:
            row = KbIngestion(draft_id=draft_id)
            session.add(row)
        row.case_id = case_id
        row.masking_version = MASKING_VERSION
        row.status = status
        row.embedded_at = embedded_at
        session.commit()


# --------------------------------------------------------------------------
# 조회(정방향)
# --------------------------------------------------------------------------
def _current_cohort(client_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "industry": client_profile["industry"],
        "district_type": client_profile["district_type"],
        "owner_gender": client_profile["owner_gender"],
        "owner_age_band": client_profile["owner_age_band"],
        "risk_appetite": client_profile["risk_appetite"],
    }


def _current_ratio_band(
    financials_pl: dict[str, Any], financials_bs: dict[str, Any]
) -> dict[str, str]:
    pl = extract_pl_metrics(financials_pl)
    bs = extract_bs_metrics(financials_bs)
    band: dict[str, str] = {}
    if "OPM" in pl:
        band["OPM"] = pct_band(pl["OPM"])
    if "DR" in bs:
        band["DR"] = ratio_band(bs["DR"])
    if "cash_runway_months" in bs:
        band["runway"] = runway_band(bs["cash_runway_months"])
    return band


def _case_from_metadata(hit: dict[str, Any]) -> dict[str, Any]:
    """CaseStore 조회 결과(평탄 메타) → rag_context.cases 항목으로 복원."""
    meta = hit["metadata"]
    lessons_raw = meta.get("expert_lessons", "")
    lessons = [l for l in lessons_raw.split(_LESSON_SEP) if l] if lessons_raw else []
    return {
        "case_id": hit["case_id"],
        "similarity": round(float(hit["similarity"]), 4),
        "cohort": {
            "industry": meta.get("industry", ""),
            "district_type": meta.get("district_type", ""),
            "owner_gender": meta.get("owner_gender", ""),
            "owner_age_band": meta.get("owner_age_band", ""),
            "risk_appetite": meta.get("risk_appetite", ""),
        },
        "situation_summary": meta.get("situation_summary", ""),
        "expert_lessons": lessons,
        "outcome": meta.get("outcome_label", "unknown"),
    }


def retrieve_rag_context(
    case_store: CaseStore,
    client_profile: dict[str, Any],
    financials_pl: dict[str, Any],
    financials_bs: dict[str, Any],
    *,
    top_k: int = 3,
    min_similarity: float = 0.75,
    now: str | None = None,
) -> dict[str, Any] | None:
    """현재 고객 코호트로 유사 과거 케이스를 조회해 rag_context 를 조립한다.

    완화 Fallback + 임계값을 적용하고, 주입 전 각 케이스를 `assert_no_pii` 로 재검증한다(이중
    방어). 잔존 PII 케이스는 드롭한다(§2.4). 남는 케이스가 없으면 None(빈 컨텍스트).
    """
    cohort = _current_cohort(client_profile)
    ratio_band_map = _current_ratio_band(financials_pl, financials_bs)
    query_text = f"{cohort['industry']} {cohort['district_type']} " + " ".join(
        ratio_band_map.values()
    )

    hits, relaxation_level, relaxed_fields = retrieve_cases(
        case_store, cohort, query_text, top_k=top_k, min_similarity=min_similarity
    )

    cases: list[dict[str, Any]] = []
    for hit in hits:
        case = _case_from_metadata(hit)
        try:
            assert_no_pii(case)  # 주입 전 방어 — 금지 키·정확 금액 스캔.
        except PiiLeak:
            continue  # 잔존 시 케이스 드롭(pii_leak).
        cases.append(case)

    if not cases:
        return None  # 임계값 미달·무결과 → 빈 컨텍스트(§2.4).

    rag_context = {
        "query": {
            "industry": cohort["industry"],
            "cohort": {
                "district_type": cohort["district_type"],
                "owner_gender": cohort["owner_gender"],
                "owner_age_band": cohort["owner_age_band"],
                "risk_appetite": cohort["risk_appetite"],
            },
            "ratio_band": ratio_band_map,
            "relaxation_level": relaxation_level,
            "relaxed_fields": relaxed_fields,
        },
        "cases": cases,
        "retrieved_at": now or _now_iso(),
    }
    validate_payload(rag_context, "rag_context")
    return rag_context
