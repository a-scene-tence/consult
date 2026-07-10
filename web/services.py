"""서비스 계층 — 생성·조회 로직 (API 라우터가 얇아지도록).

REST 엔드포인트가 호출하는 비즈니스 로직을 모은다. Strict Rule 준수: 재무 수치는 오직
`compute_pl`/`compute_bs` 로만 확정하며, 여기서는 저장/조회/조립만 한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select

from compute._common import validate_payload
from compute.compute_bs import compute_bs, pl_context_from_pl
from compute.compute_pl import compute_pl
from db.models import (
    PARSING_TARGET_SHEETS,
    Client,
    ExpertFeedback,
    Financials,
    ParsingMemory,
    PublishedReport,
    ReportDraft,
)

# POST /api/clients 요청에서 Client 컬럼으로 매핑할 프로필 필드.
_PROFILE_COLUMNS: tuple[str, ...] = (
    "trade_name", "industry", "district_type", "location_raw",
    "owner_gender", "owner_age", "owner_age_band", "risk_appetite",
)
# client_profile 스키마 검증 대상(‘name’ 은 대표자명으로 스키마 밖 — 제외).
_PROFILE_SCHEMA_FIELDS: tuple[str, ...] = (
    "trade_name", "industry", "district_type", "location_raw",
    "owner_gender", "owner_age", "owner_age_band", "risk_appetite", "onboarded_at",
)


def create_client(session_factory: Any, body: dict[str, Any]) -> int:
    """CRM 고객 프로필을 검증·저장하고 client_id(int)를 반환한다.

    `body` = 대표자명(name) + client_profile 필드. client_id 는 DB가 부여하므로 검증 시
    임시 placeholder 를 채워 `client_profile` 스키마로 검증한다.
    """
    profile = {k: body[k] for k in _PROFILE_SCHEMA_FIELDS if k in body}
    profile["client_id"] = body.get("client_id", "C-PENDING")
    validate_payload(profile, "client_profile")  # 위반 시 ValueError → 400

    name = body.get("name")
    if not name:
        raise ValueError("대표자명(name)은 필수입니다.")

    columns: dict[str, Any] = {k: body[k] for k in _PROFILE_COLUMNS if k in body}
    if "onboarded_at" in body and body["onboarded_at"]:
        columns["onboarded_at"] = datetime.fromisoformat(body["onboarded_at"])

    with session_factory() as session:
        client = Client(name=name, **columns)
        session.add(client)
        session.commit()
        return client.id


def list_clients(session_factory: Any) -> list[dict[str, Any]]:
    """등록된 고객 목록을 조종석 드롭다운용 최소 필드로 반환한다.

    `client_id` 문자 라벨은 DB에 저장되지 않고 `orchestrator._client_profile` 과 동일하게
    `f"C-{id}"` 로 파생한다(라벨 규칙 일관성). value=정수 id / data-cid=문자 라벨 브리지의 원천.
    """
    with session_factory() as session:
        rows = session.execute(select(Client).order_by(Client.id)).scalars().all()
        return [{"id": c.id, "client_id": f"C-{c.id}", "name": c.name} for c in rows]


def ingest_financials(
    session_factory: Any,
    client_id: int,
    period: str,
    pl_raw: dict[str, Any],
    bs_raw: dict[str, Any],
) -> int:
    """Master raw → compute_pl/bs 확정 → Financials 저장 → ReportDraft(computed) 생성.

    compute 의 스키마/0-나눗셈/구조 오류는 `ValueError` 로 정규화해 전파(→400). draft_id 반환.
    """
    try:
        financials_pl = compute_pl(pl_raw)
        financials_bs = compute_bs(bs_raw, pl_context=pl_context_from_pl(financials_pl))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"Master 데이터 형식 오류: {exc}") from exc

    with session_factory() as session:
        session.add(Financials(
            client_id=client_id, period=period, kind="pl", payload_json=financials_pl))
        session.add(Financials(
            client_id=client_id, period=period, kind="bs", payload_json=financials_bs))
        draft = ReportDraft(
            client_id=client_id, period=period, version=1, status="computed")
        session.add(draft)
        session.commit()
        return draft.id


def draft_exists(session_factory: Any, draft_id: int) -> bool:
    with session_factory() as session:
        return session.get(ReportDraft, draft_id) is not None


def get_draft_view(store: Any, draft_id: int) -> dict[str, Any] | None:
    """초안 검토 뷰(A-3)용 페이로드 — 초안·모순 플래그·확정 수치·프로필·Follow-up."""
    if not draft_exists(store.session_factory, draft_id):
        return None
    draft = store.get_draft(draft_id)
    inputs = store.load_inputs(draft_id)
    payload = draft.get("payload") or {}
    return {
        "draft_id": draft_id,
        "status": draft["status"],
        "version": draft["version"],
        "payload": payload,
        "contradiction_flags": payload.get("contradiction_flags", []),
        "financials_pl": inputs.get("financials_pl"),
        "financials_bs": inputs.get("financials_bs"),
        "client_profile": inputs.get("client_profile"),
        "followup_context": inputs.get("followup_context"),
    }


def record_feedback(session_factory: Any, draft_id: int, feedback: dict[str, Any]) -> None:
    """전문가 피드백을 expert_feedback 에 저장(KB 교훈·감사 근거)."""
    with session_factory() as session:
        session.add(ExpertFeedback(
            draft_id=draft_id,
            reviewer=feedback.get("reviewer", "unknown"),
            instructions_json=feedback.get("instructions", []),
            overall_note=feedback.get("overall_note"),
        ))
        session.commit()


def get_dashboard(session_factory: Any, client_id: int) -> dict[str, Any] | None:
    """고객용 대시보드 페이로드(최신 발행본)."""
    with session_factory() as session:
        stmt = (
            select(PublishedReport.dashboard_payload_json)
            .join(ReportDraft, PublishedReport.draft_id == ReportDraft.id)
            .where(ReportDraft.client_id == client_id)
            .order_by(PublishedReport.published_at.desc(), PublishedReport.id.desc())
            .limit(1)
        )
        row = session.execute(stmt).first()
        return row[0] if row else None


# 파싱 시 Agent 0 프롬프트에 주입할 승인 메모리 필드(<approved_memory>).
_MEMORY_FIELDS: tuple[str, ...] = ("raw_text", "standard_key", "target_sheet", "korean_name")


def save_parsing_memory(
    session_factory: Any,
    *,
    client_id: str | None,
    raw_text: str,
    standard_key: str,
    target_sheet: str,
    korean_name: str | None,
    approved_by: str,
) -> int:
    """전문가 승인 매핑(원시→표준 Key/시트)을 upsert 하고 memory_id 를 반환한다.

    `client_id=None` 이면 전역(공통) 매핑. `target_sheet` 는 화이트리스트로 검증(오염 방지,
    위반 시 ValueError→400). (client_id, raw_text) 동일 시 갱신(멱등).
    """
    if target_sheet not in PARSING_TARGET_SHEETS:
        raise ValueError(
            f"target_sheet '{target_sheet}' 는 허용되지 않습니다. "
            f"허용: {', '.join(PARSING_TARGET_SHEETS)}"
        )
    if not raw_text or not standard_key:
        raise ValueError("raw_text·standard_key 는 필수입니다.")

    with session_factory() as session:
        existing = session.execute(
            select(ParsingMemory).where(
                ParsingMemory.client_id.is_(client_id) if client_id is None
                else ParsingMemory.client_id == client_id,
                ParsingMemory.raw_text == raw_text,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.standard_key = standard_key
            existing.target_sheet = target_sheet
            existing.korean_name = korean_name
            existing.approved_by = approved_by
            session.commit()
            return existing.id
        row = ParsingMemory(
            client_id=client_id, raw_text=raw_text, standard_key=standard_key,
            target_sheet=target_sheet, korean_name=korean_name, approved_by=approved_by,
        )
        session.add(row)
        session.commit()
        return row.id


def load_parsing_memory(session_factory: Any, client_id: str | None) -> list[dict[str, Any]]:
    """전역(client_id NULL) + 해당 고객 승인 매핑을 합쳐 주입용 dict 리스트로 반환한다."""
    with session_factory() as session:
        stmt = select(ParsingMemory).where(
            or_(ParsingMemory.client_id.is_(None), ParsingMemory.client_id == client_id)
        ).order_by(ParsingMemory.client_id.is_(None).desc(), ParsingMemory.id)
        rows = session.execute(stmt).scalars().all()
        return [{f: getattr(r, f) for f in _MEMORY_FIELDS} for r in rows]
