-- 0001_init.sql
-- SPEC §3.2 데이터 저장 테이블 초기 마이그레이션 (PostgreSQL).
-- db/models.py 의 SQLAlchemy 모델과 컬럼/제약이 일치해야 한다.
-- 실행: psql "$DATABASE_URL" -f db/migrations/0001_init.sql

BEGIN;

-- 고객 마스터
CREATE TABLE IF NOT EXISTS clients (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 확정 수치(원천). compute_pl / compute_bs 산출 JSON 스냅샷.
CREATE TABLE IF NOT EXISTS financials (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period       VARCHAR(32)  NOT NULL,
    kind         VARCHAR(8)   NOT NULL,
    payload_json JSONB        NOT NULL,
    computed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_financials_kind CHECK (kind IN ('pl', 'bs'))
);
CREATE INDEX IF NOT EXISTS ix_financials_client_period ON financials (client_id, period, kind);

-- 리포트 초안/버전. status 는 SPEC §3.1 상태 머신.
CREATE TABLE IF NOT EXISTS report_drafts (
    id           SERIAL PRIMARY KEY,
    client_id    INTEGER      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period       VARCHAR(32)  NOT NULL,
    version      INTEGER      NOT NULL DEFAULT 1,
    status       VARCHAR(32)  NOT NULL DEFAULT 'computed',
    payload_json JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_report_status CHECK (
        status IN ('computed', 'drafting', 'review_pending', 'revising', 'approved', 'published', 'failed')
    )
);
CREATE INDEX IF NOT EXISTS ix_report_drafts_client_period ON report_drafts (client_id, period);

-- 전문가 피드백 (HITL Step 5).
CREATE TABLE IF NOT EXISTS expert_feedback (
    id                SERIAL PRIMARY KEY,
    draft_id          INTEGER      NOT NULL REFERENCES report_drafts(id) ON DELETE CASCADE,
    reviewer          VARCHAR(128) NOT NULL,
    instructions_json JSONB        NOT NULL,
    overall_note      TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 발행 완료본 (고객 대시보드 페이로드).
CREATE TABLE IF NOT EXISTS published_reports (
    id                     SERIAL PRIMARY KEY,
    draft_id               INTEGER      NOT NULL REFERENCES report_drafts(id) ON DELETE RESTRICT,
    dashboard_payload_json JSONB        NOT NULL,
    published_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 감사 로그 (에이전트 run).
CREATE TABLE IF NOT EXISTS agent_runs (
    id                BIGSERIAL PRIMARY KEY,
    draft_id          INTEGER     REFERENCES report_drafts(id) ON DELETE SET NULL,
    agent             VARCHAR(64) NOT NULL,
    input_hash        VARCHAR(64),
    output_hash       VARCHAR(64),
    model             VARCHAR(64),
    latency_ms        INTEGER,
    validation_passed BOOLEAN,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agent_runs_draft ON agent_runs (draft_id);

COMMIT;
