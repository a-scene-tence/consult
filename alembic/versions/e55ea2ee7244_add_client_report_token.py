"""add clients.report_token (public report share token — IDOR defense, Phase 15)

Revision ID: e55ea2ee7244
Revises: b7e2c9a4d18f
Create Date: 2026-07-10 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = 'e55ea2ee7244'
down_revision: Union[str, None] = 'b7e2c9a4d18f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 기존 행 호환을 위해 우선 nullable 로 컬럼 추가.
    op.add_column('clients', sa.Column('report_token', sa.String(length=32), nullable=True))
    # 2) 기존 행에 추측 불가 토큰(uuid hex)을 행별로 backfill.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id FROM clients WHERE report_token IS NULL")
    ).fetchall()
    for (client_id,) in rows:
        bind.execute(
            sa.text("UPDATE clients SET report_token = :tok WHERE id = :id"),
            {"tok": uuid4().hex, "id": client_id},
        )
    # 3) 유니크 인덱스 생성(공개 조회 키). 모델 index=True 자동명과 일치.
    op.create_index('ix_clients_report_token', 'clients', ['report_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_clients_report_token', table_name='clients')
    op.drop_column('clients', 'report_token')
