#!/usr/bin/env bash
# 컨테이너 기동 훅 (Phase 7) — DB 준비 대기 → alembic 마이그레이션 → (선택) 사용자 시드 → CMD exec.
set -euo pipefail

echo "[entrypoint] DATABASE_URL=${DATABASE_URL:-<unset>}"

# 1) DB 준비 대기(최대 60초).
python - <<'PY'
import os, time
from sqlalchemy import create_engine
url = os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit("[entrypoint] DATABASE_URL 미설정")
for attempt in range(30):
    try:
        create_engine(url).connect().close()
        print("[entrypoint] DB 연결 성공")
        break
    except Exception as exc:  # noqa
        print(f"[entrypoint] DB 대기중({attempt+1}/30): {exc}")
        time.sleep(2)
else:
    raise SystemExit("[entrypoint] DB 연결 실패")
PY

# 2) 스키마 마이그레이션(웹 컨테이너만 수행하도록 RUN_MIGRATIONS 로 제어).
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi

# 3) (선택) 기본 사용자 시드.
if [ "${SEED_USERS:-0}" = "1" ]; then
  echo "[entrypoint] 기본 사용자 시드"
  python scripts/seed_users.py || true
fi

echo "[entrypoint] exec: $*"
exec "$@"
