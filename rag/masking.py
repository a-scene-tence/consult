"""마스킹 v2 — RAG 적재/주입 전 PII 제거 (CLAUDE.md §3.5, SPEC §2.6·§3.4).

지속 학습 루프의 **철벽 방어선**. 발행 자산을 `past_consulting_cases`에 적재하기 전(그리고
`rag_context`로 주입하기 전) 개인정보를 제거·밴드화한다.

절대 원칙(§3.5 a):
- **상호명(trade_name) 완전 삭제** — 부분 마스킹(○○치킨)도 금지, 텍스트에서도 스크럽.
- **정확 위치(location_raw) 삭제 → 상권 밴드(district_type)만 유지.**
- **정확 나이(owner_age) 삭제 → 연령대 밴드(owner_age_band)만 유지.**
- 직접 식별자(client_id·연락처)·정확 금액 → 삭제/밴드화.

이중 방어(§3.5 b): `build_past_case`(적재 전) + `assert_no_pii`(적재 전 + 주입 전 양쪽)에서
잔존 PII를 스캔해 `PiiLeak`로 중단한다.
"""

from __future__ import annotations

import math
import re
from typing import Any

from compute._common import load_schema, validate_payload
from compute.compute_metrics import extract_bs_metrics, extract_pl_metrics

MASKING_VERSION = "v2"

# 금지 필드(레코드/컨텍스트에 절대 존재해선 안 되는 직접 식별자·PII).
_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"trade_name", "location_raw", "owner_age", "client_id"}
)

# 텍스트 내 정확 금액 토큰: 숫자(+쉼표) + 선택적 스케일(억/만/천) + 통화(원/₩).
# 통화/스케일 단위가 있어야 금액으로 간주(순수 수량/개수는 건드리지 않음).
_AMOUNT_RE = re.compile(r"\d[\d,]*\s*(억|만|천)?\s*(원|₩)")
# assert_no_pii 스캔용(동일 패턴 — 잔존 금액 검출).
_AMOUNT_SCAN_RE = _AMOUNT_RE


class PiiLeak(Exception):
    """마스킹 후에도 PII/정확 금액/금지 키가 잔존할 때 발생(§3.5 b)."""


# --------------------------------------------------------------------------
# 밴드 헬퍼 — 정확 수치를 코호트 벤치마크 밴드로 일반화
# --------------------------------------------------------------------------
def pct_band(value: float, width: int = 5) -> str:
    """비율(%)을 width 폭 밴드로. 예: 14.2, width 5 → '10-15%'."""
    lo = int(math.floor(value / width) * width)
    return f"{lo}-{lo + width}%"


def ratio_band(value: float, width: int = 20) -> str:
    """부채비율 등 큰 비율을 넓은 폭 밴드로. 예: 122.1 → '120-140%'."""
    return pct_band(value, width=width)


def runway_band(value: float) -> str:
    """현금 생존개월을 정수 구간 밴드로. 예: 2.7 → '2-3개월'."""
    lo = int(math.floor(value))
    return f"{lo}-{lo + 1}개월"


def generalize_period(period: str) -> str:
    """정확 회차를 반기 단위로 일반화. 예: '2025-Q3' → '2025-하반기'."""
    m = re.match(r"(\d{4})\D*Q?([1-4])", period)
    if not m:
        return period  # 이미 일반화되었거나 형식 미상 — 그대로.
    year, q = m.group(1), int(m.group(2))
    half = "상반기" if q <= 2 else "하반기"
    return f"{year}-{half}"


# --------------------------------------------------------------------------
# 텍스트 마스킹 — 상호명 스크럽, 정확 금액 블라인드
# --------------------------------------------------------------------------
def _pii_terms(client_profile: dict[str, Any]) -> list[str]:
    """텍스트에서 스크럽할 PII 어휘(상호명·정확 위치·위치 토큰)."""
    terms: list[str] = []
    for key in ("trade_name", "location_raw"):
        val = client_profile.get(key)
        if val:
            terms.append(str(val))
            # 위치는 토큰(예: '역삼동')도 개별 스크럽.
            terms.extend(tok for tok in str(val).split() if len(tok) >= 2)
    # 긴 어휘부터 치환(부분 문자열 잔존 방지).
    return sorted(set(terms), key=len, reverse=True)


def scrub_terms(text: str, terms: list[str]) -> str:
    """지정 어휘를 텍스트에서 완전 제거(○○ 표시로 치환)."""
    out = text
    for term in terms:
        if term:
            out = out.replace(term, "○○")
    return out


def mask_amounts_in_text(text: str) -> str:
    """텍스트 내 정확 금액을 블라인드 밴드로 치환. 예: '1,200만 원' → '○○만원대'.

    스케일 단위(억/만/천)가 있으면 '○○<단위>원대', 없으면 '○○원'으로 블라인드한다.
    순수 수량(개·명 등)은 통화/스케일 단위가 없어 건드리지 않는다.
    """

    def _repl(m: re.Match[str]) -> str:
        scale = m.group(1)
        return f"○○{scale}원대" if scale else "○○원"

    return _AMOUNT_RE.sub(_repl, text)


def mask_text(text: str, client_profile: dict[str, Any]) -> str:
    """텍스트에 상호명/위치 스크럽 + 정확 금액 블라인드를 모두 적용."""
    return mask_amounts_in_text(scrub_terms(text, _pii_terms(client_profile)))


# --------------------------------------------------------------------------
# 레코드 빌더 — 발행 자산 → past_case (마스킹·코호트 메타)
# --------------------------------------------------------------------------
def _financial_profile(
    financials_pl: dict[str, Any], financials_bs: dict[str, Any]
) -> dict[str, str]:
    """확정 비율을 밴드로 일반화한 코호트 재무 프로필."""
    pl = extract_pl_metrics(financials_pl)
    bs = extract_bs_metrics(financials_bs)
    return {
        "OPM_band": pct_band(pl.get("OPM", 0.0)),
        "DR_band": ratio_band(bs.get("DR", 0.0)),
        "runway_band": runway_band(bs.get("cash_runway_months", 0.0)),
    }


def _situation_summary(published: dict[str, Any], client_profile: dict[str, Any]) -> str:
    """발행 리포트 첫 섹션을 마스킹해 상황 요약으로 사용."""
    sections = published.get("sections") or published.get("report_sections") or []
    body = sections[0]["body_md"] if sections else ""
    return mask_text(body, client_profile)


def _expert_lessons(
    published: dict[str, Any],
    expert_feedback: dict[str, Any] | None,
    client_profile: dict[str, Any],
) -> list[str]:
    """전문가 반영 내역·권고에서 마스킹된 교훈을 추출."""
    lessons: list[str] = []
    for applied in published.get("applied_feedback", []):
        how = applied.get("how_applied")
        if how:
            lessons.append(mask_text(how, client_profile))
    for rec in published.get("recommendations", []):
        text = rec.get("text")
        if text:
            lessons.append(mask_text(text, client_profile))
    if expert_feedback and expert_feedback.get("overall_note"):
        lessons.append(mask_text(expert_feedback["overall_note"], client_profile))
    return lessons


def build_past_case(
    published: dict[str, Any],
    expert_feedback: dict[str, Any] | None,
    client_profile: dict[str, Any],
    financials_pl: dict[str, Any],
    financials_bs: dict[str, Any],
    *,
    case_id: str,
    outcome_label: str = "success",
) -> dict[str, Any]:
    """발행 자산을 마스킹 v2 규격의 past_case 레코드로 변환한다.

    상호명·정확위치·정확나이·client_id 는 **레코드에 포함하지 않는다**(코호트 밴드만 유지).
    반환 전 `past_case.json` 스키마로 검증한다.
    """
    cohort_meta = {
        "industry": client_profile["industry"],
        "district_type": client_profile["district_type"],
        "owner_gender": client_profile["owner_gender"],
        "owner_age_band": client_profile["owner_age_band"],
        "risk_appetite": client_profile["risk_appetite"],
    }
    period = financials_pl.get("meta", {}).get("period", "")
    record = {
        "case_id": case_id,
        "cohort_meta": cohort_meta,
        "period_generalized": generalize_period(period),
        "financial_profile": _financial_profile(financials_pl, financials_bs),
        "situation_summary": _situation_summary(published, client_profile),
        "expert_lessons": _expert_lessons(published, expert_feedback, client_profile),
        "outcome_label": outcome_label,
        "masking_version": MASKING_VERSION,
    }
    validate_payload(record, "past_case")
    return record


# --------------------------------------------------------------------------
# PII 스캐너 — 이중 방어(적재 전 + 주입 전)
# --------------------------------------------------------------------------
def _iter_strings(obj: Any) -> list[str]:
    """중첩 구조에서 모든 문자열 leaf를 수집."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: list[str] = []
        for v in obj.values():
            out.extend(_iter_strings(v))
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for item in obj:
            out.extend(_iter_strings(item))
        return out
    return []


def _iter_keys(obj: Any) -> list[str]:
    """중첩 구조에서 모든 dict 키를 수집(금지 키 검출용)."""
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(k)
            keys.extend(_iter_keys(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            keys.extend(_iter_keys(item))
    return keys


def assert_no_pii(
    record: Any,
    *,
    trade_name: str | None = None,
    location_raw: str | None = None,
    owner_age: int | None = None,
) -> None:
    """레코드에 PII/정확 금액/금지 키가 잔존하면 `PiiLeak`를 던진다(§3.5 b).

    적재 전(build 직후)과 주입 전(rag_context 조립 시) 양쪽에서 호출한다.
    """
    # (1) 금지 키 부재
    leaked_keys = _FORBIDDEN_KEYS.intersection(_iter_keys(record))
    if leaked_keys:
        raise PiiLeak(f"금지 식별자 키 잔존: {sorted(leaked_keys)}")

    strings = _iter_strings(record)

    # (2) 상호명·정확 위치(및 토큰) 부재
    terms: list[str] = []
    if trade_name:
        terms.append(str(trade_name))
        terms.extend(tok for tok in str(trade_name).split() if len(tok) >= 2)
    if location_raw:
        terms.append(str(location_raw))
        terms.extend(tok for tok in str(location_raw).split() if len(tok) >= 2)
    for text in strings:
        for term in terms:
            if term and term in text:
                raise PiiLeak(f"PII 어휘 잔존: '{term}'")

    # (3) 정확 나이 부재('34세'/'34살')
    if owner_age is not None:
        for text in strings:
            if f"{owner_age}세" in text or f"{owner_age}살" in text:
                raise PiiLeak(f"정확 나이 잔존: '{owner_age}세/살'")

    # (4) 정확 금액 부재(숫자+원/₩)
    for text in strings:
        if _AMOUNT_SCAN_RE.search(text):
            raise PiiLeak(f"정확 금액 잔존: '{_AMOUNT_SCAN_RE.search(text).group()}'")
