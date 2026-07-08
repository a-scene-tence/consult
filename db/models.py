"""PostgreSQL 테이블 접근 계층 (SQLAlchemy 2.0 declarative).

SPEC §3.2 의 데이터 저장 테이블을 1:1로 모델링한다. 상태(status)와 종류(kind)는
CHECK 제약으로 강제하여, 애플리케이션 버그가 잘못된 상태 전이를 저장하지 못하게 한다.

이 모듈은 Phase 1에서 "정의"만 하며, 실제 DB 연결/마이그레이션 실행은 후속 Phase에서
`db/migrations/0001_init.sql` 로 수행한다(라이브 Postgres 필요).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 이식성: Postgres 는 JSONB(운영), 그 외(SQLite 테스트)는 범용 JSON 으로 컴파일된다.
# DDL/운영 동작은 Postgres 에서 JSONB 그대로 유지된다(마이그레이션 SQL 무변경).
_JSON = JSON().with_variant(JSONB, "postgresql")
# BigInteger PK 는 SQLite 에서 자동증가가 안 되므로 테스트 방언에선 Integer 로 컴파일한다.
_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")

# SPEC §3.1 상태 머신에서 허용되는 report_drafts.status 값.
REPORT_STATUSES: tuple[str, ...] = (
    "computed",
    "drafting",
    "review_pending",
    "revising",
    "approved",
    "published",
    "failed",
)

# financials.kind 허용 값 (PL / BS).
FINANCIALS_KINDS: tuple[str, ...] = ("pl", "bs")

# kb_ingestions.status 허용 값 — 지속 학습 적재 잡의 멱등성·재시도 추적 (SPEC §3.4).
KB_INGESTION_STATUSES: tuple[str, ...] = ("pending", "success", "failed")

# --- v0.3 CRM 프로필 enum (SPEC §2.6 client_profile, §3.2) ---
# 상권 유형.
DISTRICT_TYPES: tuple[str, ...] = (
    "office",
    "residential",
    "floating",
    "university",
    "tourist",
    "industrial",
    "etc",
)
# 대표자 성별.
OWNER_GENDERS: tuple[str, ...] = ("male", "female", "other")
# 리스크 수용 성향.
RISK_APPETITES: tuple[str, ...] = ("conservative", "moderate", "aggressive")

# --- v0.3 recommendations enum (SPEC §3.2) ---
# 권고 목표 방향.
RECOMMENDATION_DIRECTIONS: tuple[str, ...] = ("increase", "decrease", "maintain")
# 권고 이행 상태.
RECOMMENDATION_STATUSES: tuple[str, ...] = (
    "proposed",
    "in_progress",
    "achieved",
    "not_achieved",
    "dropped",
)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """CHECK 제약용 IN 절 문자열 생성."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


class Base(DeclarativeBase):
    pass


class Client(Base):
    """고객 마스터 + CRM 프로필 (SPEC §2.6 client_profile, §3.2).

    프로필은 에이전트 맞춤 분석·RAG 코호트 검색의 기준이 된다. `trade_name`(상호),
    `location_raw`(정확 위치), `owner_age`(정확 나이)는 **내부 전용 PII** — RAG 적재 시
    삭제/밴드화된다(`CLAUDE.md §3.5`).
    """

    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(
            _in_clause("district_type", DISTRICT_TYPES), name="ck_clients_district_type"
        ),
        CheckConstraint(
            _in_clause("owner_gender", OWNER_GENDERS), name="ck_clients_owner_gender"
        ),
        CheckConstraint(
            _in_clause("risk_appetite", RISK_APPETITES), name="ck_clients_risk_appetite"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # 대표자명
    # CRM 프로필 (등록 시 필수 — 기존 행 호환을 위해 컬럼 자체는 nullable)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 상호(PII)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    district_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    location_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 내부 전용(PII)
    owner_gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    owner_age: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 내부 전용(PII)
    owner_age_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_appetite: Mapped[str | None] = mapped_column(String(16), nullable=True)
    onboarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    financials: Mapped[list["Financials"]] = relationship(back_populates="client")
    report_drafts: Mapped[list["ReportDraft"]] = relationship(back_populates="client")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="client")


class Financials(Base):
    """확정 수치(원천). compute_pl / compute_bs 산출 JSON 스냅샷을 보관한다."""

    __tablename__ = "financials"
    __table_args__ = (
        CheckConstraint(_in_clause("kind", FINANCIALS_KINDS), name="ck_financials_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # 'pl' | 'bs'
    payload_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="financials")


class ReportDraft(Base):
    """리포트 초안/버전. status 는 SPEC §3.1 상태 머신을 따른다."""

    __tablename__ = "report_drafts"
    __table_args__ = (
        CheckConstraint(_in_clause("status", REPORT_STATUSES), name="ck_report_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="computed")
    payload_json: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="report_drafts")
    feedback: Mapped[list["ExpertFeedback"]] = relationship(back_populates="draft")


class ExpertFeedback(Base):
    """전문가 피드백. HITL Step 5 — Agent 4 재작성의 최우선 입력(P3)."""

    __tablename__ = "expert_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    instructions_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    overall_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    draft: Mapped["ReportDraft"] = relationship(back_populates="feedback")


class PublishedReport(Base):
    """발행 완료본. 고객 대시보드 페이로드를 보관한다."""

    __tablename__ = "published_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("report_drafts.id", ondelete="RESTRICT"), nullable=False
    )
    dashboard_payload_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRun(Base):
    """감사 로그. 각 에이전트 run 의 입력/출력 해시, 모델, 지연, 검증 결과(CLAUDE.md §2.2)."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True)
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_drafts.id", ondelete="SET NULL"), nullable=True
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KbIngestion(Base):
    """지속 학습 적재 감사 로그 (SPEC §3.2, §3.4).

    `published` 발행 직후 비동기로 `past_consulting_cases`에 케이스를 적재한 이력.
    멱등성(동일 draft_id 1회만 적재)을 위해 `draft_id`에 UNIQUE 제약을 둔다.
    status(pending/success/failed)로 재시도를 추적한다.
    """

    __tablename__ = "kb_ingestions"
    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_kb_ingestions_draft"),
        CheckConstraint(
            _in_clause("status", KB_INGESTION_STATUSES), name="ck_kb_ingestions_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    masking_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class Recommendation(Base):
    """회차별 권고 (SPEC §2.6 recommendations, §3.2, §1.5 Follow-up).

    Agent 4 발행 시 구조화 저장되며, 다음 회차 compute가 `metric_progress`로 이행 성과를
    산출하고 전문가/시스템이 `status`를 갱신한다(시계열 전후 비교의 기준).
    """

    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint(
            _in_clause("direction", RECOMMENDATION_DIRECTIONS),
            name="ck_recommendations_direction",
        ),
        CheckConstraint(
            _in_clause("status", RECOMMENDATION_STATUSES),
            name="ck_recommendations_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("report_drafts.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    rec_code: Mapped[str] = mapped_column(String(32), nullable=False)  # 예: R-2025Q3-01
    text: Mapped[str] = mapped_column(Text, nullable=False)
    target_metric: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    client: Mapped["Client"] = relationship(back_populates="recommendations")
