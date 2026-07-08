"""임베딩 문서 생성 (SPEC §1.4).

past_case 레코드에서 **임베딩 대상 텍스트**를 만든다. 상황 요약 + 전문가 교훈을 결합해
유사도 검색의 의미 표현으로 사용한다. 실제 임베딩(벡터화)은 CaseStore(ChromaDB 기본 임베딩)가
수행하며, 여기서는 그 입력 문서만 결정론적으로 조립한다.
"""

from __future__ import annotations

from typing import Any


def case_document(record: dict[str, Any]) -> str:
    """past_case 레코드 → 임베딩 대상 문서(마스킹된 텍스트만)."""
    parts: list[str] = [record.get("situation_summary", "")]
    parts.extend(record.get("expert_lessons", []))
    return "\n".join(p for p in parts if p)
