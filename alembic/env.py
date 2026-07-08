"""Alembic 마이그레이션 환경 (Phase 6).

`db.models.Base.metadata` 를 target_metadata 로 삼아 `alembic revision --autogenerate` 가
모델 변경을 자동 감지한다. DB URL 은 환경변수 `DATABASE_URL`(운영 PostgreSQL)에서 읽으며,
없으면 alembic.ini 의 sqlalchemy.url 폴백을 사용한다.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# db.models 를 임포트해 모든 테이블이 Base.metadata 에 등록되게 한다.
from db.models import Base

config = context.config

# 환경변수 우선 — .env/컨테이너에서 실제 DATABASE_URL 주입.
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """오프라인(SQL 스크립트 생성) 모드."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """온라인(엔진 연결) 모드."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
