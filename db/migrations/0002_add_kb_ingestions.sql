-- 0002_add_kb_ingestions.sql
-- 지속 학습 적재 감사 테이블 kb_ingestions 추가 (SPEC §3.2, §3.4).
-- db/models.py 의 KbIngestion 모델과 컬럼/제약이 일치해야 한다.
-- 실행: psql "$DATABASE_URL" -f db/migrations/0002_add_kb_ingestions.sql

BEGIN;

CREATE TABLE IF NOT EXISTS kb_ingestions (
    id              SERIAL PRIMARY KEY,
    draft_id        INTEGER      NOT NULL REFERENCES report_drafts(id) ON DELETE CASCADE,
    case_id         VARCHAR(64),
    masking_version VARCHAR(32),
    embedded_at     TIMESTAMPTZ,
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
    CONSTRAINT uq_kb_ingestions_draft UNIQUE (draft_id),
    CONSTRAINT ck_kb_ingestions_status CHECK (status IN ('pending', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_kb_ingestions_status ON kb_ingestions (status);

COMMIT;
