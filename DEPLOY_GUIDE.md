# 배포 가이드 (Deploy Guide)

영세사업자 재무/손익 컨설팅 자동화 시스템을 **명령어 한 줄**로 띄우는 방법입니다.
가장 간단한 **단일 컨테이너 SQLite 스택**을 기준으로 안내합니다.

---

## 1. 사전 준비 — `.env` 파일 작성

저장소 루트에 `.env` 파일을 만들고 아래 값을 채웁니다. (`.env.example` 참고)

```dotenv
# [필수] Anthropic API 키 — LLM 에이전트 호출에 사용
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx

# [권장] JWT 서명 비밀키 — 운영에서는 반드시 임의의 긴 값으로 교체
JWT_SECRET=change-me-to-a-long-random-secret

# [선택] 데모 계정(admin/owner) 자동 시드. 운영 배포 시 0 권장
SEED_USERS=1
```

> `DATABASE_URL` 과 `CHROMA_PERSIST_DIR` 은 compose 가 컨테이너에 자동 주입하므로
> `.env` 에 넣지 않아도 됩니다. (`.env` 에 넣으면 compose 의 값이 우선합니다.)

---

## 2. 구동 — 한 줄 명령

```bash
docker-compose -f docker-compose.sqlite.yml up -d --build
```

- 최초 기동 시 컨테이너가 자동으로 `alembic upgrade head`(DB 스키마 생성)와
  (`SEED_USERS=1`이면) 데모 계정 시드를 수행한 뒤 서버를 띄웁니다.
- 로그 확인: `docker-compose -f docker-compose.sqlite.yml logs -f consult_app`
- 중지: `docker-compose -f docker-compose.sqlite.yml down`

---

## 3. 접속

| 화면 | URL |
|------|-----|
| 백오피스(관리자 조종석) | http://localhost:8000/static/index.html |
| 사장님 모바일 리포트 | 백오피스에서 고객 선택 → **"카카오톡 공유 링크 복사"** 버튼으로 생성된 `?token=...` 링크 |

- 데모 계정: **admin / admin-secret** (관리자). 운영에서는 시드를 끄고 실제 계정을 발급하세요.

### 로그인이 안 될 때
로그인 실패는 대부분 **DB에 계정이 시드되지 않은** 경우입니다.
- 단일 컨테이너/프로덕션 compose 는 `SEED_USERS=1` 이라 기동 시 자동으로 `admin` 계정이 생성됩니다.
- **로컬에서 직접 실행**(`uvicorn main:app`)한다면 시드가 없어 로그인이 안 됩니다. 아래 중 하나로 해결:
  ```bash
  SEED_USERS=1 uvicorn main:app --host 0.0.0.0 --port 8000   # 기동 시 테이블 생성+계정 시드
  # 또는 수동 시드
  python scripts/seed_users.py
  ```
  `SEED_USERS=1` 이면 앱이 부팅 시 테이블을 보장하고 `admin/admin-secret` 을 시드합니다(멱등).

---

## 4. 데이터 지속(Persistence)

컨테이너를 재시작·재빌드해도 아래 호스트 디렉터리에 데이터가 보존됩니다.

| 볼륨(호스트) | 컨테이너 경로 | 내용 |
|--------------|---------------|------|
| `./db_data`     | `/app/db`     | SQLite DB 파일(`consult.db`) — 고객·확정 수치·리포트 |
| `./chroma_data` | `/app/chroma` | ChromaDB RAG 데이터(과거 컨설팅 사례) |

> 이 두 디렉터리는 백업 대상입니다. 삭제하면 데이터가 사라집니다.

---

## 5. (참고) 프로덕션 대규모 스택

Postgres + Celery 워커 + Redis + Flower(모니터링)로 구성된 확장형 스택은 별도로 제공됩니다.

```bash
docker-compose up -d --build          # docker-compose.yml (Postgres/Celery/Redis/Flower)
```

- 비동기 워커·다중 인스턴스가 필요한 운영 트래픽에 적합합니다.
- 단일 컨테이너 SQLite 스택은 소규모·데모·온프레미스 빠른 구동에 적합합니다.
