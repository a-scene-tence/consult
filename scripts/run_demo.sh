#!/usr/bin/env bash
# 데모 원클릭 런처 — 환경변수 설정 + 프리플라이트(기동 점검) + uvicorn(포그라운드).
#
# 사용:  bash scripts/run_demo.sh
# API 키 없이 결정론적 데모(DEMO_MODE)로 조종석·리포트를 끝까지 클릭해 볼 수 있다.
# 실패하면 프리플라이트가 진짜 원인(임포트 오류·의존성 미설치 등)을 그대로 출력한다.

set -u
cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-sqlite:///./demo.db}"
export SEED_USERS="${SEED_USERS:-1}"
export DEMO_MODE="${DEMO_MODE:-1}"
export JWT_SECRET="${JWT_SECRET:-demo-secret-change-me}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "[run_demo] DATABASE_URL=$DATABASE_URL  DEMO_MODE=$DEMO_MODE  SEED_USERS=$SEED_USERS"
echo "[run_demo] 프리플라이트: 앱 임포트/기동 점검..."
if ! python -c "from web.app import create_app; create_app(); print('[run_demo] APP OK')"; then
  echo ""
  echo "[run_demo] ❌ 앱 기동 실패 — 위 오류(traceback)를 확인하세요."
  echo "[run_demo]    의존성 미설치면:  pip install -e ."
  echo "[run_demo]    저장소 루트에서 실행 중인지도 확인(현재: $(pwd))."
  exit 1
fi

echo "[run_demo] ✅ 기동 OK → uvicorn 시작 (Ctrl+C 로 종료)"
echo "[run_demo]    조종석:      http://localhost:${PORT}/cockpit   (로그인 admin / admin-secret)"
echo "[run_demo]    사장님 리포트: 조종석의 '카카오톡 공유 링크 복사' 버튼으로 생성"
echo "[run_demo]    (Codespaces 는 전달된 ${PORT} 포트 URL 로 접속)"
exec uvicorn main:app --host "$HOST" --port "$PORT"
