# CLAUDE.md — 작업 및 코딩 규칙

> **문서 상태:** 개정 v0.3 (전면 개정, 승인 대기)
> 이 파일은 Claude Code 및 모든 기여자가 이 저장소에서 작업할 때 따르는 **최우선 규칙**이다.
> 상세 제품 사양은 `SPEC.md`(v0.3), UI는 `DESIGN.md`, 이슈 이력은 `BUGS_AND_LOGS.md` 참조.
>
> **v0.3 개정 요지:** 연산 레이어 의무 사전 계산 목록(§3.0 — 품목 마진·BEP·CCC·DSCR·Runway),
> RAG 코호트 필터링 강제(§2.4), 마스킹 v2(상호명 삭제·위치/나이 밴드화, §3.5).

---

## 0. 절대 원칙 (Golden Rules)

1. **에이전트 무연산(Strict Rule).** LLM 에이전트 레이어에서 숫자를 계산·추정·반올림·비교연산하지 않는다. 품목 마진·BEP·CCC·DSCR 등 **모든 파생 지표까지** Python(Pandas)/DB에서 확정하고, 에이전트는 확정 JSON의 값만 **인용**한다.
2. **역할 경계 준수.** 각 에이전트는 `SPEC.md §2`에 정의된 책임 범위 밖을 판단하지 않는다.
3. **인간 피드백 최우선.** `expert_feedback`가 있으면 Agent 4는 초안보다 피드백을 우선 반영한다.
4. **문서 우선(Docs-first).** 코드 수정 전에 관련 MD 문서(SPEC/DESIGN)를 먼저 최신화한다. 승인 없이 코드 착수 금지.
5. **전문가 게이트(P5).** 시스템에 유입되는 수치는 전문가가 정제한 표준화 Master 양식을 통해서만 들어온다. 원시 데이터를 직접(사람 검토 없이) compute 파이프라인에 넣는 코드를 만들지 않는다.
   - **(Phase 9) P5 자동 표준화 보조(assist).** LLM 데이터 엔지니어(Agent 0, `agents/data_engineer.py`)가 비표준 원시 데이터를 9대 표준 스키마로 파싱해 **표준화 Master 초안**을 만들 수 있다. 단 이 초안은 **전문가가 검토·보완·승인한 뒤에만** `POST /api/consulting/ingest`로 유입된다 — 사람이 최종 게이트임은 불변. Agent 0도 **무연산**(의미론적 매핑·정규화·동적 스키마 확장만)이며, 집계·총합 대사는 `compute/reconcile.py`가 Python으로 확정한다. 파싱 방어선은 `numeric_guard`가 아니라 **reconciliation**(원시 총합 대조, 불일치 시 `reconciliation_error`)이다. 표준 밖 데이터는 `schema_extensions`에 격리 보존하고 compute에 자동 주입하지 않는다.
     - **(Phase 10) 승인 매핑 메모리(학습형, Rule #0).** 전문가가 승인한 '원시 텍스트 → 표준 Key/시트' 매핑을 `parsing_memory` 테이블에 영속화하고, 다음 파싱 시 **전역(공통) + 해당 고객** 메모리를 Agent 0 프롬프트의 `<approved_memory>`로 주입한다(v1.1 Rule #0 = 승인 매핑 **강제 재사용**). 목적은 회차 간 매핑 드리프트·환각 억제(일관성 락)이며 **무연산 원칙 불변**. 전문가 승인분만 신뢰 주입하고, `target_sheet`는 화이트리스트(`db.models.PARSING_TARGET_SHEETS`: 9대 블록 + `schema_extensions`)로 검증(위반 시 400)해 **메모리 오염**을 방지한다. API: `POST /api/consulting/parse/memory`(저장), `GET .../parse/memory`(조회).

---

## 1. 기술 스택

| 계층 | 기술 | 비고 |
|------|------|------|
| 데이터 처리 / 연산 | **Python 3.11+, Pandas** | PL/BS + 파생 지표(P*Q·BEP·CCC·DSCR·Runway) 확정 계산의 유일 원천 |
| 데이터베이스 | **PostgreSQL** | CRM 프로필·확정 수치·초안·피드백·권고(recommendations)·발행본·감사로그 |
| 지식기반 RAG | **ChromaDB** | `past_consulting_cases` 컬렉션 — 발행 후 **자동 임베딩 적재**(마스킹 v2 필수), Agent 1·2가 **코호트 필터 + 유사도**로 조회. 임베딩 모델 기본값은 로컬/오프라인 기본 임베딩(확정은 구현 단계). §2.4·§3.5 |
| LLM | **Claude Messages API** (`claude-opus-4-8`) | adaptive thinking, structured outputs |
| 오케스트레이션 | **커스텀 Python** | LangGraph 미사용 (§2 참조) |
| 프론트엔드 | **HTML + TailwindCSS** | 관리자 백오피스 + 고객 대시보드 (반응형) |
| 설정/비밀 | **.env (python-dotenv)** | `ANTHROPIC_API_KEY` 등. 세무 캘린더·RAG 임계값 등 파라미터 포함 |

> 공식 Anthropic Python SDK(`anthropic`)를 사용한다. 기본 모델 문자열은 정확히 `claude-opus-4-8`.
> 원시 HTTP·OpenAI 호환 shim 사용 금지.

---

## 2. 오케스트레이션 구현 방향

### 2.1 왜 커스텀 Python인가 — 변경 없음

- **HITL 중단/재개가 핵심.** 상태를 PostgreSQL에 영속화해야 재시작·확장에 안전.
- **Strict Rule 강제.** 수치 주입·검증 훅을 완전 통제. LangGraph/LangChain 미도입.

### 2.2 오케스트레이터 설계

- 상태 머신은 `report_drafts.status`(`SPEC.md §3.1`)로 표현, 각 전이는 명시적 함수.
- 각 에이전트 = 단일 책임 함수/클래스 (`agents/pl_analyst.py` 등 4종).
- 오케스트레이터(`orchestrator.py`)가: (1) DB에서 상태·입력 로드 → (2) **컨텍스트 조립**
  (`client_profile` + `followup_context` + `rag_context`) → (3) 에이전트 호출 → (4) 출력 검증(§3)
  → (5) DB 기록 → (6) 다음 전이.
- **Follow-up 조립(§SPEC 1.5):** Agent 1·2 호출 전, 직전 회차 `recommendations`와 compute가
  산출한 `metric_progress`를 `followup_context`로 묶어 주입. 직전 회차가 없으면
  `is_first_round=true`(baseline 모드) — 예외 처리 누락 금지(`followup_missing_baseline`).
- 모든 에이전트 호출은 `agent_runs`에 감사 로그 기록.
- Agent 1·2 병렬 호출 가능. Agent 3은 둘을 기다린다.
- Agent 1·2 호출 직전에 지속 학습 조회(§2.4)로 `rag_context` 구성.
- `published` 전이 직후: (a) **`recommendations` 구조화 저장**(다음 회차 Follow-up 기준),
  (b) **비동기 KB 적재 훅**(§2.4) 호출.

### 2.3 Claude 호출 규약 — 변경 없음

- `client.messages.create(model="claude-opus-4-8", ...)`, 구조화 출력 강제, 프리필 금지,
  SDK 재시도 + 애플리케이션 레벨 재생성 후 `failed`.

### 2.4 지속 학습(Continuous Learning) 파이프라인 & 코호트 필터링

- **적재(역방향, 발행 직후 비동기):**
  1. `published` 직후 비동기 트리거(대시보드 응답 비차단).
  2. `published_reports` + `expert_feedback` 로드 → **마스킹 v2**(§3.5) →
     `past_consulting_cases` 레코드 생성 — **코호트 메타데이터**(industry, district_type,
     owner_gender, owner_age_band, risk_appetite)를 ChromaDB 메타 필드로 저장 → 임베딩 → upsert.
  3. 멱등성(동일 draft_id 1회) + `kb_ingestions` 감사 + 지수 백오프 재시도. 마스킹 검증 실패 시 중단.
- **조회(정방향, Agent 1·2 시작 시) — 코호트 필터링 강제:**
  1. **메타데이터 필터 우선 적용**: 업종 + 상권 유형(district_type) + 대표자 성별·연령대 밴드 +
     리스크 성향으로 후보를 좁힌 뒤, 재무 비율 밴드 유사도로 top_k 선별.
     단순 업종-only 검색 금지(이종 상권/타겟 벤치마크 오염 방지, `category_mismatch`).
  2. 코호트 일치 케이스가 부족하면 필터를 단계적으로 완화(성향 → 성별 → 연령대 순)하되,
     완화 수준을 `rag_context.query`에 기록해 에이전트가 참고 강도를 조절하게 한다.
  3. 최소 유사도 임계값 미달 시 미주입(빈 컨텍스트).
  4. 주입 전 마스킹 재검증(이중 방어, §3.5).
- 임계값·top_k·완화 정책·임베딩 모델은 `.env`/설정으로 관리, 기본값은 보수적으로.

---

## 3. Strict Rule 코딩 가드레일

### 3.0 연산 레이어 의무 사전 계산 목록 (LLM 호출 전 확정)

compute 모듈은 LLM 호출 **전에** 아래 지표를 **의무적으로** 계산해 확정 JSON에 포함해야 한다.
하나라도 누락되면 에이전트가 계산 유혹에 빠진다 — 누락 시 파이프라인을 진행하지 않는다.

**PL (`compute/compute_pl.py`):**
| 지표 | 정의 |
|------|------|
| 품목별 단위당 마진 | `unit_margin = selling_price − unit_cost`, `margin_pct` |
| 품목별 공헌이익 | `contribution_margin = unit_margin × quantity` |
| 마진율 정렬 | `margin_rank`(best/mid/worst) — 효자/부진 품목 Top·Bottom |
| 판관비 증가율 Top3 | `opex_details[].increase_rank`(1~3, 그 외 null) |
| 현금 매출 누락 | `unallocated_cash_sales`{amount, pct_of_revenue, risk_flag(임계값 기반)} |
| **BEP 매출액** | `bep_revenue = fixed_cost ÷ 가중 공헌이익률` (가중 공헌이익률 = Σ(P−C)Q ÷ ΣPQ) |
| **일일 최소 타겟 판매수량** | `daily_target_qty = ceil(bep_revenue ÷ business_days ÷ 주력상품 P)` — 주력상품(anchor_item)은 공헌이익 최대 품목 |
| BEP 달성률 | `bep_attainment_pct = REV ÷ bep_revenue × 100` |

**BS (`compute/compute_bs.py`):**
| 지표 | 정의 |
|------|------|
| `cash_conversion_cycle` | 재고자산회전일수 + AR일수(`ar_days`) − AP일수(`ap_days`) |
| `dscr` | (영업이익 + 감가상각비) ÷ 월 원리금 총액(`Σ debt_details[].monthly_payment`) |
| `cash_runway_months` | 가용현금(CASH) ÷ 월 고정비 |
| 세무 이벤트 | `upcoming_tax_events` — 세무 캘린더(정적 설정: 1·7월 부가세 확정, 4·10월 예정, 5월 종소세 등)와 리포트 period 대조, `months_until` 산출 |

**Follow-up (`compute` + 오케스트레이터):**
| 지표 | 정의 |
|------|------|
| `metric_progress` | 직전 회차 확정치 vs 당기 확정치 비교(direction: improved/worsened/flat) — 에이전트가 아니라 compute가 산출 |

- 세무 캘린더는 **정적 설정**(코드 상수 또는 설정 테이블)로 관리한다. Agent 2는 산출된
  `upcoming_tax_events`를 인용만 한다.
- DSCR의 감가상각비는 Master 양식의 판관비 세부(`opex_details`)에서 식별한다(계정 표준화는 P5 게이트 책임).
- 0-나눗셈 방어 필수: 판매수량 0, 전기 0, CL/EQUITY/월 원리금 0 등 → 명시적 에러 또는 N/A 처리
  (`division_by_zero`, `BUGS_AND_LOGS.md`).

### 3.1 레이어 분리 — 변경 없음

```
[연산 레이어]  Pandas/DB  →  financials.pl / financials.bs (+파생 지표, 유일 수치 원천)
[에이전트 레이어]  LLM  →  서술/해석/권고만 (숫자 인용만)
[검증 훅]  numeric_guard()  →  출력 수치가 입력 Golden Set에 존재하는지 대조
```

### 3.2 검증 훅 `numeric_guard()` 규약

- 기존 규약 유지(허용 오차 0, 비수치 표현 허용, 최후 방어선).
- **Golden Set 확장:** `financials.*` 값 + `followup_context.metric_progress` 값 +
  `client_profile`의 수치(owner_age 등)를 포함한다. 그 밖의 수치는 전부 위반.

### 3.3 스키마 검증 — 변경 없음 (`schemas/*.json`, 위반 시 `failed`)

### 3.4 역할 경계 검증 — 변경 없음

### 3.5 RAG·마스킹 가드레일 v2 (지속 학습)

- **(a) 이중 마스킹(적재 전 + 주입 전, `rag/masking.py`):**
  - **상호명(`trade_name`) 완전 삭제** — 부분 마스킹(○○치킨)도 금지, 필드 자체 제거.
  - **위치 밴드화:** `location_raw` 삭제, `district_type`(상권 밴드)만 유지.
    예: '강남구 역삼동' → '오피스 상권'.
  - **나이 밴드화:** `owner_age`(정확 나이) 삭제, `owner_age_band`만 유지.
    예: '34세' → '30대 초중반'.
  - 성별은 코호트 메타로만 사용(자유 텍스트 서술에서 개인 특정 문맥 제거).
  - 직접 식별자(고객명·client_id·사업자번호·연락처) 삭제, 정확 금액·비율 → 밴드.
- **(b) 주입 전 검증:** `rag_context`에 상호명·정확 위치·정확 나이·PII·정확 금액이 없는지 스캔.
  잔존 시 케이스 드롭 + `pii_leak` 이슈 기록.
- **(c) 정성 참고 원칙:** `numeric_guard`는 현재 고객 수치에만 적용. RAG 유래 밴드/과거 수치를
  현재 확정치로 인용하면 위반(P1).
- **(d) 코호트 오염 방지:** 조회 시 코호트 필터(§2.4) 필수 — 이종 상권/성별/연령 케이스가
  벤치마크로 섞이면 `category_mismatch` 이슈로 기록.
- **(e) 신선도·품질:** `outcome_label`·`embedded_at` 기반 임계값·정리 주기 관리(`rag_contamination`).

---

## 4. 디렉터리 구조 (안)

```
consult/
├── SPEC.md / CLAUDE.md / DESIGN.md / BUGS_AND_LOGS.md
├── .env.example                 # 키 + 세무캘린더/RAG 파라미터
├── pyproject.toml
├── schemas/                     # JSON Schema (I/O 계약)
│   ├── client_profile.json      # CRM 프로필 (신규)
│   ├── followup_context.json    # Follow-up 주입 계약 (신규)
│   ├── financials_pl.json       # 고도화(P*Q·BEP 포함)
│   ├── financials_bs.json       # 고도화(CCC·DSCR·Runway·세무 이벤트 포함)
│   ├── agent1_pl_analysis.json  # 확장(bep_guidance·tax_risk_alerts·adherence_review)
│   ├── agent2_bs_analysis.json  # 확장(cash_reserve_alerts·adherence_review)
│   ├── agent3_draft_report.json
│   ├── expert_feedback.json
│   ├── agent4_final_report.json # + recommendations[] 추출
│   ├── recommendations.json     # 회차 권고 계약 (신규)
│   ├── dashboard_payload.json   # hero_kpis(BEP) 반영
│   ├── rag_context.json         # 코호트 쿼리 반영
│   └── past_case.json           # cohort_meta 반영
├── db/
│   ├── migrations/              # DDL (clients 프로필 확장, recommendations, parsing_memory)
│   └── models.py
├── compute/                     # 연산 레이어 (§3.0 의무 목록)
│   ├── ingest.py                # Master 엑셀/CSV → 정형화 (P5 게이트 산출물만 수용)
│   ├── compute_pl.py            # P*Q·BEP 포함
│   ├── compute_bs.py            # CCC·DSCR·Runway·세무 이벤트 포함
│   ├── tax_calendar.py          # 정적 세무 캘린더 (신규)
│   └── reconcile.py             # (Phase 9) parser_output → Master 초안 + 총합 대사(Python 집계)
├── agents/                      # LLM 레이어 (무연산)
│   ├── base.py / pl_analyst.py / bs_analyst.py / report_master.py / final_publisher.py
│   └── data_engineer.py         # (Phase 9) Agent 0 — 원시 데이터 → 9대 표준 스키마 파싱(P5 보조)
├── rag/
│   ├── chroma_client.py         # 코호트 메타 필터 조회
│   ├── masking.py               # 마스킹 v2 (§3.5)
│   ├── embed.py
│   └── case_indexer.py
├── guards/
│   └── numeric_guard.py
├── orchestrator.py              # 컨텍스트 조립(profile+followup+rag) 포함
├── web/  (backoffice/, dashboard/)
└── tests/
```

> 위 구조는 제안이며, 코드 착수 승인 시 확정한다. 기존 Phase 1·2 코드는 본 개정 승인 후
> 고도화 스키마에 맞춰 재정비한다.

---

## 5. 코딩 컨벤션 — 변경 없음

(네이밍 영어, 타입 힌트 필수, 예외 미은폐, .env 비밀 관리, 연산 결정론, 로깅 최소 PII)

- **추가:** `location_raw`·`owner_age`·`trade_name` 등 내부 전용 PII 필드는 로그에 남기지 않는다.

---

## 6. 작업 진행 프로세스 — 변경 없음

(문서 최신화 우선 → 브랜치 고정 → 명확한 커밋 → 테스트 원칙 → 이슈 기록 → PR은 요청 시)

- **테스트 원칙 추가:** §3.0 의무 지표(BEP·CCC·DSCR·Runway·margin_rank·metric_progress)는
  각각 골든 케이스 단위 테스트 필수. 0-나눗셈 경계 테스트 필수.

---

## 7. 정의된 완료(Definition of Done) — 기능 단위

- [ ] 관련 스키마(`schemas/`)와 SPEC 계약이 일치한다.
- [ ] 연산 레이어 골든 테스트 통과 — **§3.0 의무 지표 전부 포함**.
- [ ] `numeric_guard`(확장 Golden Set) 및 스키마 검증 통과.
- [ ] 역할 경계 위반 없음.
- [ ] HITL 상태 전이가 `SPEC.md §3.1`과 일치.
- [ ] (Follow-up) 신규 고객 baseline 모드 예외 처리 존재.
- [ ] (지속 학습) 마스킹 v2 검증 통과 — `rag_context`·적재 레코드에 상호명/정확 위치/정확 나이/PII/정확 금액 부재, 코호트 메타 존재.
- [ ] 관련 MD 문서 최신화 완료.
