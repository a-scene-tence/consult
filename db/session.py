"""DB 엔진·세션 팩토리 (SPEC §3.2, CLAUDE.md §2.1).

오케스트레이터의 상태 영속화가 의존하는 SQLAlchemy 엔진/세션을 만든다. 운영은 `DATABASE_URL`
(PostgreSQL), 테스트는 SQLite in-memory 로 동작한다. 모델의 `_JSON`/`_BIGINT_PK` 방언 variant
덕분에 동일 모델이 양쪽에서 작동한다.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base


def make_engine(url: str | None = None, **kwargs: Any):
    """엔진 생성. url 미지정 시 `DATABASE_URL` 환경변수 사용."""
    resolved = url or os.environ.get("DATABASE_URL")
    if not resolved:
        raise ValueError("DATABASE_URL 이 설정되지 않았습니다(또는 url 인자를 넘기세요).")
    return create_engine(resolved, future=True, **kwargs)


def make_session_factory(engine) -> sessionmaker:
    """세션 팩토리(sessionmaker) 생성."""
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def create_all(engine) -> None:
    """모든 테이블 생성(테스트/부트스트랩용). 운영은 db/migrations SQL 사용."""
    Base.metadata.create_all(engine)
