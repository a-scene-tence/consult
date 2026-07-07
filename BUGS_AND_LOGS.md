# BUGS_AND_LOGS.md — 이슈 및 오류 트래커

> **문서 상태:** 초안 v0.1 (승인 대기)
> **목적:** 멀티 에이전트 협업 과정에서 발생하는 환각·컨텍스트 누락·로직 오류·역할 위반 등을
> 기록하고, 동일 문제의 재발을 방지한다. 새 이슈는 상단(최신순)에 추가한다.

---

## 1. 이슈 기록 템플릿

새 이슈는 아래 블록을 복사해 채운다.

```
### [BUG-####] <한 줄 제목>
- 일시: YYYY-MM-DD HH:MM (KST)
- 심각도: Critical | High | Medium | Low
- 카테고리: hallucination | context_loss | role_violation | contradiction | feedback_ignored | schema_violation | compute_error | infra
- 관련 에이전트/모듈: Agent 1(PL) | Agent 2(BS) | Agent 3(리포트마스터) | Agent 4(발행가) | compute | orchestrator | web | db | rag
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
- [ ] ChromaDB RAG 컨텍스트가 오래되면 벤치마크 왜곡 → 지식기반 갱신 주기 관리 필요.
- [ ] 큰 `max_tokens` 재작성 시 타임아웃 → 스트리밍 사용 검토.
- [ ] 통화/기간 포맷 로케일 이슈(KRW, 분기 표기) → 표시 계층 단위 테스트.
