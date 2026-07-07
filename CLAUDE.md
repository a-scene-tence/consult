# CLAUDE.md — 작업 및 코딩 규칙

> **문서 상태:** 초안 v0.1 (승인 대기)
> 이 파일은 Claude Code 및 모든 기여자가 이 저장소에서 작업할 때 따르는 **최우선 규칙**이다.
> 상세 제품 사양은 `SPEC.md`, UI는 `DESIGN.md`, 이슈 이력은 `BUGS_AND_LOGS.md` 참조.

---

## 0. 절대 원칙 (Golden Rules)

1. **에이전트 무연산(Strict Rule).** LLM 에이전트 레이어에서 숫자를 계산·추정·반올림·비교연산하지 않는다. 모든 재무 수치는 Python(Pandas)/DB에서 확정하고, 에이전트는 확정 JSON의 값만 **인용**한다.
2. **역할 경계 준수.** 각 에이전트는 `SPEC.md §2`에 정의된 책임 범위 밖을 판단하지 않는다.
3. **인간 피드백 최우선.** `expert_feedback`가 있으면 Agent 4는 초안보다 피드백을 우선 반영한다.
4. **문서 우선(Docs-first).** 코드 수정 전에 관련 MD 문서(SPEC/DESIGN)를 먼저 최신화한다. 승인 없이 코드 착수 금지.

---

## 1. 기술 스택

| 계층 | 기술 | 비고 |
|------|------|------|
| 데이터 처리 / 연산 | **Python 3.11+, Pandas** | PL/BS 확정 수치 계산의 유일 원천 |
| 데이터베이스 | **PostgreSQL** | 확정 수치·초안·피드백·발행본·감사로그 |
| 지식기반 RAG | **ChromaDB** | 업종 벤치마크·컨설팅 노하우 임베딩 검색 |
| LLM | **Claude Messages API** (`claude-opus-4-8`) | adaptive thinking, structured outputs |
| 오케스트레이션 | **커스텀 Python** | LangGraph 미사용 (§2 참조) |
| 프론트엔드 | **HTML + TailwindCSS** | 관리자 백오피스 + 고객 대시보드 (반응형) |
| 설정/비밀 | **.env (python-dotenv)** | `ANTHROPIC_API_KEY` 등 |

> 공식 Anthropic Python SDK(`anthropic`)를 사용한다. 기본 모델 문자열은 정확히 `claude-opus-4-8`.
> 원시 HTTP·OpenAI 호환 shim 사용 금지.

---

## 2. 오케스트레이션 구현 방향

### 2.1 왜 커스텀 Python인가

- **HITL 중단/재개가 핵심.** 워크플로가 사람의 피드백을 기다리며 며칠 멈출 수 있다. 상태를 프레임워크 메모리가 아니라 **PostgreSQL에 영속화**해야 재시작·확장에 안전하다.
- **Strict Rule 강제.** 수치 주입·검증 훅을 우리가 완전히 통제해야 한다. 추상화 계층이 얇을수록 디버깅과 감사가 쉽다.
- LangGraph/LangChain은 도입하지 않는다. (필요 시 향후 재검토하되 지금은 의존성 최소화.)

### 2.2 오케스트레이터 설계

- 상태 머신은 `report_drafts.status`(`SPEC.md §3.1`)로 표현하고, 각 전이는 **명시적 함수**로 구현.
- 각 에이전트 = **단일 책임 함수/클래스** (`agents/pl_analyst.py`, `agents/bs_analyst.py`, `agents/report_master.py`, `agents/final_publisher.py`).
- 오케스트레이터(`orchestrator.py`)가: (1) DB에서 상태·입력 로드 → (2) 에이전트 호출 → (3) 출력 검증(§3) → (4) DB에 결과·상태 기록 → (5) 다음 전이.
- 모든 에이전트 호출은 `agent_runs` 테이블에 감사 로그(입력/출력 해시, 모델, 지연, 검증 결과) 기록.
- Agent 1·2는 서로 의존하지 않으므로 **병렬 호출** 가능. Agent 3은 둘의 결과를 기다린다.

### 2.3 Claude 호출 규약

- `client.messages.create(model="claude-opus-4-8", thinking={"type": "adaptive"}, ...)`.
- 구조화 출력: `output_config={"format": {"type": "json_schema", "schema": ...}}`로 스키마 강제.
- 확정 수치는 프롬프트에 JSON 주입(또는 tool-use로 DB 조회). 프리필(assistant 선행 turn) 사용 금지(400).
- 재시도: SDK 기본 재시도에 의존하고, 검증 실패는 애플리케이션 레벨에서 최대 N회 재생성 후 `failed` 처리.

---

## 3. Strict Rule 코딩 가드레일

### 3.1 레이어 분리

```
[연산 레이어]  Pandas/DB  →  financials.pl / financials.bs (JSON, 유일 수치 원천)
                                   │  (읽기 전용 주입)
[에이전트 레이어]  LLM  →  서술/해석/권고만 생성 (숫자는 인용만)
                                   │
[검증 훅]  numeric_guard()  →  에이전트 출력의 모든 수치가 입력 JSON 값 집합에 존재하는지 대조
```

### 3.2 검증 훅 `numeric_guard()` 규약

- 에이전트 출력(구조화 필드 + 본문 텍스트)에서 숫자·비율·통화 금액을 추출.
- 각 값이 해당 run의 입력 JSON 값 집합(`allowed_values`)에 **정확히** 존재하는지 확인(허용 오차 0).
- 하나라도 불일치하면: 해당 run `validation_passed=false`, 상태 `failed`, 재생성 트리거 또는 사람 개입.
- 예외적으로 "N/A", "데이터 없음" 같은 비수치 표현은 허용.
- 이 훅은 **환각으로 만들어낸 숫자**를 잡는 최후 방어선이다. 반드시 모든 에이전트 출력에 적용.

### 3.3 스키마 검증

- 모든 에이전트 입출력은 `schemas/*.json`(JSON Schema)로 검증(`jsonschema` 라이브러리).
- 스키마 위반 시 즉시 `failed` 처리, `BUGS_AND_LOGS.md`에 기록.

### 3.4 역할 경계 검증

- 각 에이전트 출력에 `out_of_scope`/책임 범위 밖 주제가 본문에 섞이지 않았는지 경량 점검(키워드/구조 기반). 위반 시 경고 로그.

---

## 4. 디렉터리 구조 (안)

```
consult/
├── SPEC.md
├── CLAUDE.md
├── DESIGN.md
├── BUGS_AND_LOGS.md
├── .env.example                 # ANTHROPIC_API_KEY 등 키 목록(값 없음)
├── pyproject.toml               # 의존성
├── schemas/                     # JSON Schema (에이전트 I/O 계약)
│   ├── financials_pl.json
│   ├── financials_bs.json
│   ├── agent1_pl_analysis.json
│   ├── agent2_bs_analysis.json
│   ├── agent3_draft_report.json
│   ├── expert_feedback.json
│   ├── agent4_final_report.json
│   └── dashboard_payload.json
├── db/
│   ├── migrations/              # PostgreSQL DDL 마이그레이션
│   └── models.py                # 테이블 접근 계층
├── compute/                     # 연산 레이어 (숫자 원천)
│   ├── ingest.py                # raw 업로드 → 정형화
│   ├── compute_pl.py
│   └── compute_bs.py
├── agents/                      # 에이전트 레이어 (LLM, 무연산)
│   ├── base.py                  # 공통 규약·프롬프트 블록·numeric_guard 연동
│   ├── pl_analyst.py            # Agent 1
│   ├── bs_analyst.py            # Agent 2
│   ├── report_master.py         # Agent 3
│   └── final_publisher.py       # Agent 4
├── rag/
│   └── chroma_client.py         # ChromaDB 인덱싱/검색
├── guards/
│   └── numeric_guard.py         # Strict Rule 검증 훅
├── orchestrator.py              # 상태 머신·파이프라인 제어
├── web/                         # HTML/Tailwind
│   ├── backoffice/              # 관리자 백오피스
│   └── dashboard/               # 고객용 대시보드
└── tests/
```

> 위 구조는 **제안**이며, 코드 착수 승인 시 확정한다.

---

## 5. 코딩 컨벤션

- **네이밍:** 코드 식별자·함수·변수·JSON 키는 영어. 사용자 대면 문자열/주석 설명은 한국어 허용.
- **타입 힌트:** 모든 공개 함수에 타입 힌트 필수. 데이터 계약은 `dataclass`/`TypedDict` 또는 `pydantic` 사용.
- **에러 처리:** 예외를 삼키지 말 것. 파이프라인 실패는 `agent_runs`/`BUGS_AND_LOGS.md`에 기록.
- **비밀 관리:** 키·토큰은 `.env`에서만 로드. 코드·커밋·로그·프롬프트에 하드코딩 금지. `.env`는 `.gitignore`.
- **결정론:** 연산 레이어는 동일 입력에 동일 출력. LLM 서술 레이어는 비결정적임을 전제로 검증 훅으로 보호.
- **로깅:** 각 에이전트 run은 입력/출력 해시, 모델, 지연, 검증 결과를 남긴다(원문 수치·PII 로그 최소화).

---

## 6. 작업 진행 프로세스

1. **문서 최신화 우선.** 기능/스키마 변경은 `SPEC.md`(계약)와 `DESIGN.md`(UI)를 먼저 갱신하고 승인받은 뒤 코드 수정.
2. **브랜치:** 모든 작업은 `claude/multi-agent-finance-consulting-u31k43`에서 진행. 다른 브랜치 푸시 금지.
3. **커밋:** 명확한 메시지. 커밋/푸시는 사용자가 요청할 때만.
4. **테스트 원칙:**
   - 연산 레이어(`compute/`): 골든 케이스 단위 테스트(입력→기대 수치) 필수.
   - `numeric_guard`: 환각 수치 주입 시 반드시 실패하는 회귀 테스트.
   - 스키마 검증: 각 에이전트 I/O 샘플로 계약 테스트.
5. **이슈 기록:** 에이전트 협업 오류(환각·컨텍스트 누락·역할 위반 등)는 `BUGS_AND_LOGS.md` 템플릿에 즉시 기록.
6. **PR:** 사용자가 명시적으로 요청할 때만 생성.

---

## 7. 정의된 완료(Definition of Done) — 기능 단위

- [ ] 관련 스키마(`schemas/`)와 SPEC 계약이 일치한다.
- [ ] 연산 레이어 골든 테스트 통과.
- [ ] `numeric_guard` 및 스키마 검증 통과.
- [ ] 역할 경계 위반 없음.
- [ ] HITL 상태 전이가 `SPEC.md §3.1`과 일치.
- [ ] 관련 MD 문서 최신화 완료.
