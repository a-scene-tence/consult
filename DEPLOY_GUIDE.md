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
git pull                                             # 최신 requirements.txt 로 빌드(의존성 고정 반영)
docker-compose -f docker-compose.sqlite.yml up -d --build
```

- 이미지는 RAG(ChromaDB)까지 포함해 다소 무겁습니다. RAG 를 쓰지 않는다면(`RAG_ENABLED` 미설정)
  `requirements.txt` 의 `chromadb` 라인을 제거해 이미지·빌드 시간을 줄일 수 있습니다.
- 최초 기동 시 컨테이너가 자동으로 `alembic upgrade head`(DB 스키마 생성)와
  (`SEED_USERS=1`이면) 데모 계정 시드를 수행한 뒤 서버를 띄웁니다.
- 로그 확인: `docker-compose -f docker-compose.sqlite.yml logs -f consult_app`
- 중지: `docker-compose -f docker-compose.sqlite.yml down`

---

## 2-A. 데모 데이터로 직접 구동 (API 키 불필요)

`ANTHROPIC_API_KEY` 없이도 조종석·모바일 리포트를 **끝까지 직접 클릭**해 보고 싶다면 `DEMO_MODE=1`
로 띄우세요. 실제 LLM 대신 **결정론적 예시 결과**를 반환해 파싱→분석→발행 전 과정이 동작합니다.

**가장 간단한 실행 — 원클릭 런처(권장):**
```bash
bash scripts/run_demo.sh    # 환경변수 설정 + 기동 점검(+의존성 자동 설치) + uvicorn 실행
```
`run_demo.sh` 는 먼저 **프리플라이트**로 앱이 정상 기동되는지 확인합니다. 의존성이 안 깔려 있으면
**`pip install -e .` 를 자동으로 실행**한 뒤 재시도하고, 그래도 실패하면 **진짜 원인(traceback)을 그대로
출력**합니다. 성공하면 uvicorn 을 띄우며 접속 주소(`/cockpit`)와 로그인 정보를 안내합니다.

> 실행 후 터미널에 **`Uvicorn running on http://0.0.0.0:8000`** 줄이 떠야 서버가 살아있는 것입니다.
> **그 줄이 안 뜨면** 위에 출력된 오류가 원인입니다. 그리고 **그 터미널 창을 닫거나 `Ctrl+C` 하면 서버가
> 죽어** 다시 다운로드/빈 화면이 됩니다(그 창을 계속 열어 두세요).

수동으로 실행하려면:
```bash
export DATABASE_URL="sqlite:///./demo.db"
export SEED_USERS=1 DEMO_MODE=1
uvicorn main:app --host 0.0.0.0 --port 8000
```

- 포트 8000 을 열고 **`/cockpit`**(또는 `/static/index.html`) 로그인(`admin` / `admin-secret`).
- **드롭다운에 데모 고객이 자동으로 등장**합니다(DEMO_MODE 가 고객이 없으면 1건 자동 시드).
- 원시 데이터 칸의 **"예시 데이터 채우기"** 버튼을 누르면 예시 텍스트가 채워집니다 →
  **파싱 → 승인 → 발행**을 직접 클릭(키 없이 동작).
- (선택) 미리 **발행된 리포트 + 공유 링크**를 바로 보고 싶다면:
  `python scripts/seed_demo.py` — 발행까지 끝낸 데모 고객을 만들고
  **사장님 리포트 공유 링크**(`/report?token=...`)를 출력합니다.

> **접속했는데 렌더가 안 되거나 다운로드/빈 화면이면** 대개 **앱이 안 떠 있는 것**입니다.
> 터미널에서 `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/cockpit` →
> `200` 이면 서버 정상(브라우저 접속 방법 문제 → 아래 2-B 참고), **아무 것도 안 나오거나 000 이면
> uvicorn 미기동**이므로 `bash scripts/run_demo.sh` 의 프리플라이트 출력을 확인하세요.

> ⚠️ **`DEMO_MODE` 는 데모 전용**입니다(고정 예시 반환 — 실제 분석 아님). 운영에서는 절대 켜지 마세요.

---

## 2-B. GitHub Codespaces 에서 실행

Codespaces 에서는 무거운 docker 빌드 대신 **uvicorn 을 직접 띄우는 것이 가장 빠릅니다**
(코어 의존만 설치 — chromadb 제외):

```bash
cd /workspaces/consult
pip install -e .                       # 최초 1회
export DATABASE_URL="sqlite:///./consult.db"
export SEED_USERS=1                     # 기동 시 테이블 생성 + admin 계정 시드
uvicorn main:app --host 0.0.0.0 --port 8000
```

그러면 Codespaces 가 **포트 8000** 을 자동 전달합니다. 포트 패널에서 8000 을 열어 접속하세요.

> ⚠️ **포트를 잘못 열면 "Cannot GET /" 만 뜹니다.** 이는 우리 앱이 아니라 **VS Code 서버 자체**의
> 응답입니다. 포트 패널의 **"실행 중인 프로세스"** 열을 보고, `/vscode/bin/...` 로 시작하는 포트(예:
> 53283 등)는 열지 마세요. **실행 프로세스가 `uvicorn`/`python` 인 8000 포트**를 여는 것이 맞습니다.
> 8000 이 목록에 아예 없다면 앱이 아직 안 뜬 것이니 위 명령의 터미널 로그를 확인하세요.

- `DATABASE_URL` 은 필수입니다(기본값 없음). SQLite 파일 경로로 지정하세요.
- `SEED_USERS=1` 이면 alembic 없이도 부팅 시 테이블·데모 계정을 만들어 바로 로그인됩니다.

### 페이지 대신 HTML 이 "다운로드" 될 때 → **십중팔구 서버가 안 떠 있는 것**
포트를 열었더니 렌더 대신 **다운로드/빈 화면**이 뜬다면, **대부분 8000 포트에 서버가 실제로 안 떠 있는
경우**입니다. Codespaces 는 서버가 죽은 포트도 목록에 남겨 두고, 그 죽은 포트로 접속하면 **빈 응답이
내려와 Safari 가 이를 파일 다운로드로 처리**합니다(우리 서버 헤더는 정상 — `Content-Type: text/html`,
첨부 헤더 없음, 실측 확인). 특히 iPad Safari 에서 PORTS 패널의 **지구본(브라우저에서 열기)** 로
`about:blank` 새 탭이 열리며 `-8000.app.github.dev` 를 다운로드하려는 팝업이 이 증상입니다.

**먼저 서버가 살아있는지 확정하세요(터미널 한 줄):**
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/cockpit
```
- **아무 것도 안 나오거나 `000`/연결 거부** → **서버 미기동**(다운로드의 진짜 원인). 아래로:
  1. **Codespace 가 켜져 있는지 확인.** idle 30분 등으로 **정지(Stopped)** 되면 포워딩 포트만 남고 서버는
     죽습니다. github.com/codespaces 에서 해당 Codespace 가 **Active** 인지 보고, 정지면 다시 여세요.
  2. 터미널에서 **`bash scripts/run_demo.sh`** 실행 → **`Uvicorn running on http://0.0.0.0:8000`** 줄이
     뜨는지 확인(그 터미널 창을 계속 열어 두기 — 닫으면 서버가 죽습니다).
  3. 그 줄이 안 뜨면 스크립트가 출력한 **오류(traceback)** 를 그대로 공유해 주세요(런처가 의존성은
     자동 설치하므로, 남는 오류가 진짜 원인입니다).
- **`200` 이 나옴** → 서버는 정상입니다. 그럼 다운로드/빈 화면은 **브라우저 접속 방법** 문제입니다.

  **📱 iPad(모바일) 정답 — 실측 확정 경로:**
  1. **PORTS 패널에서 8000 → Port Visibility → Public** 으로 전환.
     (Public 은 URL 을 아는 누구나 접근 가능 → 데모/개인용만, 끝나면 Private 로 되돌리기.)
  2. **일반 Safari 새 탭**에서 **https 전달 URL** 을 직접 여세요(끝에 `/cockpit`):
     `https://<코드스페이스이름>-8000.app.github.dev/cockpit` → 조종석 렌더(로그인 admin/admin-secret).
  - ⚠️ iPad 에서는 **`http://localhost:8000` 이 렌더되지 않습니다.** github.dev 에디터는 https 인데
    iPad Safari 웹뷰가 **https 안의 `http://localhost`(비보안)를 혼합 콘텐츠로 차단**하기 때문입니다
    (그래서 Simple Browser 가 **빈 화면**이 됩니다). 반드시 **https 전달 URL** 을 쓰세요.
  - 사장님 리포트도 같은 https 도메인의 `.../report?token=...` (조종석 "공유 링크 복사" 버튼이 생성).

  **🖥️ 데스크톱 VS Code 전용 — Simple Browser:**
  `Ctrl/Cmd+Shift+P` → **"Simple Browser: Show"** → `http://localhost:8000/cockpit` 입력 → 에디터 안에서
  렌더됩니다. (이 방법은 **데스크톱 VS Code 에서만** 동작하며, iPad 웹뷰에서는 위 혼합 콘텐츠 차단으로
  빈 화면이 되니 iPad 는 위 https 경로를 쓰세요.)

---

## 3. 접속

| 화면 | URL |
|------|-----|
| 관리자 조종석(로컬) | http://localhost:8000/cockpit |
| 관리자 조종석(Codespaces/iPad) | https://&lt;코드스페이스이름&gt;-8000.app.github.dev/cockpit (포트 Public) |
| 사장님 모바일 리포트 | 조종석에서 고객 선택 → **"카카오톡 공유 링크 복사"** 버튼으로 생성된 `.../report?token=...` 링크 |

- 데모 계정: **admin / admin-secret** (관리자). 운영에서는 시드를 끄고 실제 계정을 발급하세요.
- **iPad/Codespaces 는 `http://localhost` 가 아니라 `https://…-8000.app.github.dev` (Public 포트)** 로
  접속하세요(위 2-B "다운로드/빈 화면" 절 참고 — 혼합 콘텐츠 차단 회피).

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
