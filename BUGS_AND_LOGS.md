# BUGS_AND_LOGS.md — 이슈 및 오류 트래커

> **문서 상태:** 개정 v0.3 (승인 대기)
> **목적:** 멀티 에이전트 협업 과정에서 발생하는 환각·컨텍스트 누락·로직 오류·역할 위반 등을
> 기록하고, 동일 문제의 재발을 방지한다. 새 이슈는 상단(최신순)에 추가한다.
> **v0.3 개정 요지:** 미시 연산/데이터 카테고리(`reconciliation_error`, `division_by_zero`),
> 시계열/RAG 카테고리(`followup_missing_baseline`, `category_mismatch`) 추가,
> `pii_leak` 위험군에 상호명·정확 위치·정확 성별/나이 명시.

---

## 1. 이슈 기록 템플릿

새 이슈는 아래 블록을 복사해 채운다.

```
### [BUG-####] <한 줄 제목>
- 일시: YYYY-MM-DD HH:MM (KST)
- 심각도: Critical | High | Medium | Low
- 카테고리: hallucination | context_loss | role_violation | contradiction | feedback_ignored | schema_violation | compute_error | infra | rag_contamination | similarity_mismatch | pii_leak | reconciliation_error | division_by_zero | followup_missing_baseline | category_mismatch | memory_poisoning
- 관련 에이전트/모듈: Agent 1(PL) | Agent 2(BS) | Agent 3(리포트마스터) | Agent 4(발행가) | compute | orchestrator | web | db | rag | masking | case_indexer | ingest(Master 게이트) | followup
- 관련 리포트: <client_id> / <period> / <version> / <draft_id>
- 상태: Open | Investigating | Fixed | Won't Fix | Regression-Guarded

**증상(관측된 것)**
- ...

**재현 절차**
1. ...
2. ...

**근본 원인**
- ...

**조치(수정 내용)**
- ...

**재발 방지(추가한 가드/테스트)**
- [ ] 회귀 테스트 추가: <경로>
- [ ] 가드/스키마 갱신: <내용>
- [ ] 문서 갱신: SPEC/CLAUDE/DESIGN 중 해당 항목
```

**심각도 기준**
- **Critical:** 잘못된 수치가 고객에게 발행됨(Strict Rule 위반), 데이터 유출, 파이프라인 정지.
- **High:** 발행 전 검출된 환각/역할 위반/피드백 미반영.
- **Medium:** 서술 품질 저하, 모순 미탐지, UI 바인딩 오류.
- **Low:** 표현·오탈자·경미한 UX.

---

## 2. 에이전트 협업 특유 오류 카테고리

| 카테고리 | 정의 | 대표 증상 | 1차 방어선 |
|----------|------|-----------|------------|
| `hallucination` | 입력 JSON에 없는 숫자를 에이전트가 생성 | 존재하지 않는 매출/비율 언급 | `numeric_guard()` (CLAUDE.md §3.2) |
| `context_loss` | 이전 단계 결과·피드백이 다음 단계에 누락 전달 | Agent 4가 특정 피드백 무시, Agent 3가 A2 결과 누락 | 오케스트레이터 입력 완전성 검사, `agent_runs` 입력 해시 대조 |
| `role_violation` | 역할 경계를 벗어난 판단 | PL 분석가가 부채 리스크를 단정 | 역할 경계 검증(CLAUDE.md §3.4), 시스템 프롬프트 규약 |
| `contradiction` | Agent 1·2 결론이 상충하는데 미탐지 | 수익성↑ + 현금경색을 종합에서 뭉갬 | Agent 3 `contradiction_flags` 강제 필드 |
| `feedback_ignored` | 전문가 지시를 Agent 4가 미반영/왜곡 | `applied_feedback`에 없음 또는 지시와 배치 | `applied_feedback` 대조, HITL 재검토 루프(P3) |
| `schema_violation` | 출력이 JSON Schema 위반 | 필드 누락/타입 오류 | `jsonschema` 검증(CLAUDE.md §3.3) |
| `compute_error` | 연산 레이어 수치 오류 | 확정 수치 자체가 틀림 | `compute/` 골든 테스트 |
| `infra` | API/DB/네트워크 오류 | 타임아웃, 429, 커넥션 | SDK 재시도, 상태 `failed` 후 재시도 |
| `rag_contamination` | 과거 데이터 오염 — 노후·부정확·저품질 케이스가 신규 분석을 왜곡 | 오래된 벤치마크·틀린 교훈이 rag_context로 유입 | 적재 시 `outcome_label`·`embedded_at` 신선도 메타, 최소 유사도 임계값, 주기적 정리(CLAUDE.md §3.5) |
| `similarity_mismatch` | 유사도 매칭 실패 — 무관 케이스가 매칭되어 잘못된 벤치마크 제공 | 다른 업종/재무구조 케이스가 top_k에 포함 | 업종 필터 + 비율 밴드 사전필터, 임계값 미달 시 `rag_context` 미주입(CLAUDE.md §2.4) |
| `pii_leak` | 마스킹 실패로 PII·정확 금액이 과거 케이스/rag_context에 잔존. **위험군: 상호명(trade_name), 정확한 위치(location_raw), 정확한 성별/나이(owner_age), 고객명·사업자번호·정확 금액** | 상호명·'34세'·'역삼동' 등이 케이스 요약에 노출 | 마스킹 v2(상호명 완전 삭제, 위치→상권 밴드, 나이→연령대 밴드), 주입 전 스캔, 잔존 시 케이스 드롭·적재 중단(CLAUDE.md §3.5) |
| `reconciliation_error` | **매출 대사 불일치** — POS 품목 매출 총합과 신고/통장 매출이 맞지 않는데 unallocated 분류 없이 진행 | 품목 합계 ≠ 총매출인데 확정 JSON 생성됨 | ingest 단계 대사 체크 → 차액은 `unallocated_cash_sales`로 강제 분류, 임계 초과 시 업로드 반려(DESIGN A-1). **(Phase 9)** `compute/reconcile.py`가 파싱 초안 생성 시 Python으로 `Σ(P×Q)` vs `source_raw_sum` 대사 — 품목 매출이 원시 총합 초과 시 `reconciliation_error`(파싱 환각), 나머지 차액은 `unallocated_cash_sales`로 도출. LLM 파서(Agent 0)는 무연산이므로 이 대사가 환각 방어선이다. |
| `division_by_zero` | 미시 연산의 0-나눗셈 — 판매수량 0, 전기 0, CL/EQUITY/월 원리금 0 등 | BEP·마진율·DSCR 계산 crash 또는 inf | compute 0-나눗셈 방어(명시 에러 또는 N/A), 경계 골든 테스트(CLAUDE.md §3.0) |
| `followup_missing_baseline` | **직전 데이터가 없는 신규 고객**에 대한 Follow-up 분석 예외 처리 누락 | 신규 고객인데 이행 점검 서술이 생성되거나 파이프라인 에러 | `is_first_round=true` baseline 모드 분기(SPEC §1.5), 오케스트레이터 필수 체크 |
| `category_mismatch` | **이종 상권/타겟 벤치마크 오염** — 코호트가 다른 케이스(다른 상권·성별·연령대·성향)가 RAG 벤치마크로 주입 | 오피스 상권 치킨집에 관광지 카페 교훈이 인용됨 | 코호트 메타 필터 강제 + 완화 수준 기록(CLAUDE.md §2.4), 주입 케이스 cohort_meta 검사 |
| `memory_poisoning` | **(Phase 10) 승인 매핑 메모리 오염** — 잘못된/미승인 매핑이 `<approved_memory>`로 주입돼 Agent 0 이 원시 데이터를 엉뚱한 표준 Key/시트로 강제 매핑(Rule #0 역효과) | '임차료'가 debt 시트로, 매입이 매출로 굳어져 회차마다 재현 | 전문가 승인분만 저장·주입, `target_sheet` 화이트리스트 검증(위반 400), (client_id, raw_text) upsert로 중복 방지(CLAUDE.md §0.5) |

---

## 3. 재발 방지 체크리스트

**모든 파이프라인 실행 전/후 확인 (자동화 목표)**

- [ ] **Strict Rule:** 에이전트 출력의 모든 수치가 입력 JSON 값 집합에 존재(`numeric_guard` 통과)했는가?
- [ ] **스키마:** 모든 에이전트 I/O가 `schemas/*.json`을 통과했는가?
- [ ] **역할 경계:** 각 에이전트 출력이 자기 책임 범위 내인가? 범위 밖은 `out_of_scope`로 표기됐는가?
- [ ] **컨텍스트 완전성:** Agent 3에 A1·A2 결과가, Agent 4에 초안·피드백·원본 수치가 모두 전달됐는가?
- [ ] **모순 점검:** Agent 3의 `contradiction_flags`가 생성됐고 미해결 항목이 검토에 노출됐는가?
- [ ] **피드백 반영:** Agent 4 `applied_feedback`가 모든 `high` 우선순위 지시를 커버했는가?
- [ ] **상태 전이:** `report_drafts.status`가 SPEC §3.1의 허용 전이만 따랐는가?
- [ ] **감사 로그:** `agent_runs`에 입력/출력 해시·모델·검증 결과가 기록됐는가?
- [ ] **비밀 관리:** 프롬프트·로그·커밋에 키/PII가 노출되지 않았는가?

**미시 연산·데이터 게이트 (ingest/compute 시)**
- [ ] **매출 대사:** POS 품목 합계 vs 총매출 차액이 `unallocated_cash_sales`로 분류됐는가(`reconciliation_error` 방지)?
- [ ] **0-나눗셈 방어:** 판매수량 0·전기 0·월 원리금 0 케이스가 명시적 에러/N/A로 처리됐는가?
- [ ] **의무 지표 완결:** §CLAUDE 3.0 목록(품목 마진·BEP·CCC·DSCR·Runway·Top3·metric_progress)이 전부 확정 JSON에 존재하는가?

**시계열(Follow-up) 가드**
- [ ] **baseline 분기:** 직전 회차가 없는 신규 고객이 `is_first_round=true` 모드로 처리됐는가(`followup_missing_baseline` 방지)?
- [ ] **이행 지표 원천:** `metric_progress`가 compute 산출 확정치인가(에이전트 계산 아님)?
- [ ] **권고 저장:** 발행 시 `recommendations`가 구조화 저장되어 다음 회차 기준이 되는가?

**지속 학습(RAG) 가드 (적재/조회 시)**
- [ ] **마스킹 v2:** 적재 전·주입 전 이중 마스킹 — **상호명 완전 삭제, 위치→상권 밴드, 나이→연령대 밴드**, PII·정확 금액 부재?
- [ ] **코호트 필터:** 조회가 업종+상권+성별/연령대+성향 메타 필터를 거쳤고, 완화 수준이 기록됐는가(`category_mismatch` 방지)?
- [ ] **유사도 임계값:** 최소 유사도 미달 시 `rag_context`를 주입하지 않았는가(빈 컨텍스트)?
- [ ] **과거 케이스 정합:** 저장된 케이스에 `client_id`/고객명/상호명/정확 위치/정확 나이/정확 금액이 없는가(코호트 메타+밴드만)?
- [ ] **오용 금지:** 에이전트가 RAG 유래 밴드/과거 수치를 현재 고객 확정치로 인용하지 않았는가(`numeric_guard` 통과)?
- [ ] **적재 멱등성:** 동일 `draft_id` 중복 적재 없이 `kb_ingestions`에 결과가 기록됐는가?

**발행(Publish) 게이트 (Critical 방지)**
- [ ] 대시보드 수치가 `financials.*` 확정값과 100% 일치(프론트 재계산 없음).
- [ ] 전문가 최종 승인(`approved`) 존재.

---

## 4. 이슈 로그 (최신순)

> 아래는 템플릿 사용법을 보여주는 **예시(placeholder)** 항목이다. 실제 운영 시 교체/추가한다.

### [BUG-0002] Agent 4가 '리스크 톤 과장 금지' 전체 코멘트를 부분 무시 (예시)
- 일시: 2026-07-07 10:20 (KST)
- 심각도: High
- 카테고리: feedback_ignored
- 관련 에이전트/모듈: Agent 4(발행가)
- 관련 리포트: C-1001 / 2025-Q2 / v2 / D-2025Q2-C1001
- 상태: Regression-Guarded

**증상(관측된 것)**
- 재작성본에서 부채비율 관련 문장이 여전히 위기감을 과장하는 어조로 남음. `overall_note`("리스크 톤을 과장하지 말 것")가 섹션별 지시보다 낮은 우선순위로 처리됨.

**재현 절차**
1. `expert_feedback.overall_note`에 톤 관련 지시 입력.
2. Agent 4 재작성 실행.
3. 결과 `body_md`에서 과장 어조 잔존 확인.

**근본 원인**
- 프롬프트에서 `overall_note`가 `instructions[]`와 동등하게 나열되어 전역 제약임이 강조되지 않음.

**조치(수정 내용)**
- Agent 4 시스템 프롬프트에서 `overall_note`를 "모든 섹션에 적용되는 전역 제약"으로 승격, 반영 결과를 `applied_feedback`에 별도 항목으로 강제.

**재발 방지(추가한 가드/테스트)**
- [x] 회귀 테스트 추가: `tests/agents/test_final_publisher_global_note.py`
- [x] 가드/스키마 갱신: `applied_feedback`에 `scope: "global"` 지원
- [x] 문서 갱신: SPEC §2.4 프롬프트 전략

---

### [BUG-0001] PL 분석가가 부채비율을 언급 (역할 경계 위반) (예시)
- 일시: 2026-07-07 09:55 (KST)
- 심각도: High
- 카테고리: role_violation
- 관련 에이전트/모듈: Agent 1(PL)
- 관련 리포트: C-1001 / 2025-Q2 / v1 / D-2025Q2-C1001
- 상태: Fixed

**증상(관측된 것)**
- Agent 1 출력 `findings`에 "부채비율이 높아 위험" 문장 포함 — BS 분석가(Agent 2) 책임 범위 침범.

**재현 절차**
1. `financials.pl`만 입력해 Agent 1 실행.
2. 출력에 부채 관련 서술 등장 확인.

**근본 원인**
- 역할 경계 규약이 시스템 프롬프트에 있으나, 입력 JSON에 부채 관련 힌트가 섞여 유도됨.

**조치(수정 내용)**
- Agent 1 입력을 `financials.pl`로 엄격 한정(부채 필드 제거), 프롬프트에 "부채/현금흐름은 본 분석 범위 아님" 명시 강화.

**재발 방지(추가한 가드/테스트)**
- [x] 회귀 테스트 추가: `tests/agents/test_pl_analyst_scope.py`
- [x] 가드/스키마 갱신: 역할 경계 키워드 점검(CLAUDE.md §3.4)
- [x] 문서 갱신: SPEC §2.1 역할 경계

---

## 5. 알려진 리스크 / 관찰 목록 (Watchlist)

정식 버그는 아니지만 모니터링이 필요한 항목.

- [ ] LLM 비결정성으로 동일 입력에도 서술 편차 → 검증 훅은 수치만 보장, 서술 품질은 리뷰로 보완.
- [ ] 큰 `max_tokens` 재작성 시 타임아웃 → 스트리밍 사용 검토.
- [ ] 통화/기간 포맷 로케일 이슈(KRW, 분기 표기) → 표시 계층 단위 테스트.
- [ ] **과거 데이터 오염(`rag_contamination`):** 노후·저품질 케이스가 신규 분석 왜곡 → 신선도 메타·outcome 라벨·주기적 정리, 임계값 관리.
- [ ] **유사도 매칭 실패(`similarity_mismatch`):** 무관 케이스 매칭 → 잘못된 벤치마크 → 코호트 필터+비율 밴드 사전필터, 임계값 미달 시 미주입.
- [ ] **코호트 벤치마크 오염(`category_mismatch`):** 코호트 데이터가 적은 초기에는 필터 완화가 잦아 이종 상권/타겟 케이스 혼입 위험 ↑ → 완화 수준 기록·참고 강도 하향, 케이스 축적 후 필터 강화.
- [ ] **임베딩 노후/드리프트:** 임베딩 모델 교체·업종 분포 변화로 유사도 품질 저하 → 재임베딩 주기·회귀 모니터링.
- [ ] **마스킹 회귀(`pii_leak`):** 마스킹 규칙 변경으로 상호명·정확 위치/나이·정확 금액 유출 → 마스킹 골든 테스트·주입 전 스캔 상시 유지.
- [ ] **Master 양식 버전 드리프트:** 표준화 양식 개정 시 구버전 업로드 혼입 → 양식 버전 필드·ingest 검증.
- [ ] **승인 매핑 메모리 오염/노후(`memory_poisoning`):** 초기 승인 매핑이 잘못되면 Rule #0 로 회차마다 오매핑 재현 → 저장 시 target_sheet 화이트리스트, 전문가 승인분만 주입, 잘못된 항목은 재승인(upsert)으로 교정. 사업 변화로 매핑이 낡으면 갱신.
- [ ] **세무 캘린더 유지보수:** 세법 개정으로 신고 일정 변경 시 정적 설정 갱신 누락 → 연 1회 이상 점검 항목화.
- [ ] **프로필 노후화:** 상권 변화·업종 전환 등 프로필 변경 미반영 → 회차 시작 시 프로필 확인 단계.
