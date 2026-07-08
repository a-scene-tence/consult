#!/usr/bin/env python
"""기본 인증 사용자 시드 (Phase 7).

`users` 테이블에 데모/부트스트랩 계정(admin, owner)을 생성한다. 비밀번호는 환경변수
(ADMIN_PASSWORD, OWNER_PASSWORD)로 재정의할 수 있다. 운영에서는 안전한 비밀번호로 교체하라.

사용법:
    export DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/consult
    python scripts/seed_users.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def main() -> None:
    from db.session import make_engine, make_session_factory
    from web.auth import seed_default_users

    session_factory = make_session_factory(make_engine())
    seed_default_users(session_factory)
    print("기본 사용자 시드 완료: admin(admin), owner(client, client_id=1)")


if __name__ == "__main__":
    main()
