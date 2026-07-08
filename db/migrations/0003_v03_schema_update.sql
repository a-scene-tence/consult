-- 0003_v03_schema_update.sql
-- v0.3 스키마 개정 (SPEC §3.2): clients CRM 프로필 확장 + recommendations 신규.
-- db/models.py 의 Client(확장)·Recommendation 모델과 컬럼/제약이 일치해야 한다.
-- 실행: psql "$DATABASE_URL" -f db/migrations/0003_v03_schema_update.sql

BEGIN;

-- 1) clients CRM 프로필 컬럼 추가 (기존 행 호환을 위해 nullable).
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS trade_name     VARCHAR(255),  -- 상호(내부 전용 PII)
    ADD COLUMN IF NOT EXISTS industry       VARCHAR(64),
    ADD COLUMN IF NOT EXISTS district_type  VARCHAR(16),
    ADD COLUMN IF NOT EXISTS location_raw   VARCHAR(255),  -- 정확 위치(내부 전용 PII)
    ADD COLUMN IF NOT EXISTS owner_gender   VARCHAR(8),
    ADD COLUMN IF NOT EXISTS owner_age      INTEGER,       -- 정확 나이(내부 전용 PII)
    ADD COLUMN IF NOT EXISTS owner_age_band VARCHAR(32),
    ADD COLUMN IF NOT EXISTS risk_appetite  VARCHAR(16),
    ADD COLUMN IF NOT EXISTS onboarded_at   TIMESTAMPTZ;

ALTER TABLE clients
    ADD CONSTRAINT ck_clients_district_type
        CHECK (district_type IN ('office', 'residential', 'floating', 'university', 'tourist', 'industrial', 'etc'));
ALTER TABLE clients
    ADD CONSTRAINT ck_clients_owner_gender
        CHECK (owner_gender IN ('male', 'female', 'other'));
ALTER TABLE clients
    ADD CONSTRAINT ck_clients_risk_appetite
        CHECK (risk_appetite IN ('conservative', 'moderate', 'aggressive'));

-- 2) recommendations — 회차별 권고(Follow-up 이행 추적 기준).
CREATE TABLE IF NOT EXISTS recommendations (
    id            SERIAL PRIMARY KEY,
    draft_id      INTEGER      NOT NULL REFERENCES report_drafts(id) ON DELETE CASCADE,
    client_id     INTEGER      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    period        VARCHAR(32)  NOT NULL,
    rec_code      VARCHAR(32)  NOT NULL,
    text          TEXT         NOT NULL,
    target_metric VARCHAR(64),
    direction     VARCHAR(16),
    status        VARCHAR(16)  NOT NULL DEFAULT 'proposed',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_recommendations_direction CHECK (direction IN ('increase', 'decrease', 'maintain')),
    CONSTRAINT ck_recommendations_status CHECK (status IN ('proposed', 'in_progress', 'achieved', 'not_achieved', 'dropped'))
);
CREATE INDEX IF NOT EXISTS ix_recommendations_client_period ON recommendations (client_id, period);

COMMIT;
