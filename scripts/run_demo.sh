#!/usr/bin/env bash
# 데모 원클릭 런처 — 환경변수 설정 + 프리플라이트(기동 점검, 의존성 자동 설치) + uvicorn(포그라운드).
#
# 사용:  bash scripts/run_demo.sh
# API 키 없이 결정론적 데모(DEMO_MODE)로 조종석·리포트를 끝까지 클릭해 볼 수 있다.
# 실패하면 프리플라이트가 진짜 원인(임포트 오류·의존성 미설치 등)을 그대로 출력한다.
# (의존성 미설치는 감지 시 pip install -e . 로 자동 설치 후 재시도한다.)

set -u
cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-sqlite:///./demo.db}"
export SEED_USERS="${SEED_USERS:-1}"
export DEMO_MODE="${DEMO_MODE:-1}"
export JWT_SECRET="${JWT_SECRET:-demo-secret-change-me}"
export JWT_EXPIRE_MINUTES="${JWT_EXPIRE_MINUTES:-720}"   # 데모 편의: 로그인 12시간 유지(운영 기본 120분)
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

_ERR="$(mktemp 2>/dev/null || echo /tmp/run_demo_preflight.err)"
trap 'rm -f "$_ERR"' EXIT

# 프리플라이트: 앱을 실제로 임포트·생성해 기동 가능한지 확인한다.
# stderr 를 파일로 캡처(원인 판별용)하고 화면에도 그대로 출력한다.
run_preflight() {
  python -c "from web.app import create_app; create_app(); print('[run_demo] APP OK')" 2>"$_ERR"
  local rc=$?
  cat "$_ERR" >&2
  return $rc
}

echo "[run_demo] DATABASE_URL=$DATABASE_URL  DEMO_MODE=$DEMO_MODE  SEED_USERS=$SEED_USERS"
echo "[run_demo] 프리플라이트: 앱 임포트/기동 점검..."

if ! run_preflight; then
  # 임포트/모듈 오류면 의존성 미설치 → 자동 설치 후 1회 재시도.
  if grep -qiE "ModuleNotFoundError|ImportError|No module named" "$_ERR"; then
    echo ""
    echo "[run_demo] 의존성 미설치 감지 → pip install -e . 자동 실행..."
    if ! pip install -e .; then
      echo ""
      echo "[run_demo] ❌ pip install 실패 — 위 오류를 확인하세요(네트워크·권한 등)."
      exit 1
    fi
    echo "[run_demo] 재시도: 프리플라이트..."
    if ! run_preflight; then
      echo ""
      echo "[run_demo] ❌ 의존성 설치 후에도 기동 실패 — 위 오류(traceback)를 확인하세요."
      echo "[run_demo]    저장소 루트에서 실행 중인지도 확인(현재: $(pwd))."
      exit 1
    fi
  else
    # 임포트 외 오류(설정·코드 등) → traceback 은 위에 그대로 출력됨.
    echo ""
    echo "[run_demo] ❌ 앱 기동 실패 — 위 오류(traceback)를 확인하세요."
    echo "[run_demo]    저장소 루트에서 실행 중인지도 확인(현재: $(pwd))."
    exit 1
  fi
fi

echo "[run_demo] ✅ 기동 OK → uvicorn 시작 (Ctrl+C 로 종료)"
echo "[run_demo]    조종석:      http://localhost:${PORT}/cockpit   (로그인 admin / admin-secret)"
echo "[run_demo]    사장님 리포트: 조종석의 '카카오톡 공유 링크 복사' 버튼으로 생성"
echo "[run_demo]    (Codespaces 는 전달된 ${PORT} 포트 URL 로 접속)"
echo "[run_demo] --------------------------------------------------------------------------"
echo "[run_demo] 아래 'Uvicorn running on ...' 줄이 뜨면 서버 UP 입니다."
echo "[run_demo] 그 줄이 안 뜨면 위 오류가 원인입니다. 이 창을 닫거나 Ctrl+C 하면 포트가 죽습니다."
echo "[run_demo] --------------------------------------------------------------------------"
exec uvicorn main:app --host "$HOST" --port "$PORT"
