"""연산 레이어 공통 유틸 — 스키마 로딩/검증, 반올림 규약.

CLAUDE.md §3.1: 연산 레이어가 산출한 JSON은 `schemas/*.json` 계약을 반드시 통과해야 한다.
반올림 규약: 비율(%)은 소수 1자리, 금액은 정수. 이 규약은 모든 compute_* 에서 공유한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# 저장소 루트 (compute/_common.py → parent.parent)
_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_DIR = _ROOT / "schemas"


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """`schemas/<name>.json` 을 로드한다. 예: load_schema("financials_pl")."""
    path = _SCHEMA_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def validate_payload(payload: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """payload 를 지정 스키마로 검증한다. 실패 시 예외(위반 내역 포함)를 던진다.

    CLAUDE.md §3.3: 스키마 위반은 즉시 실패 처리한다(예외를 삼키지 않는다).
    """
    validator = Draft202012Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        messages = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
        )
        raise ValueError(f"[{schema_name}] 스키마 검증 실패: {messages}")
    return payload


def round_pct(value: float) -> float:
    """비율(%)을 소수 1자리로 반올림."""
    return round(float(value), 1)


def to_money(value: float) -> int:
    """금액을 정수로 확정."""
    return int(round(float(value)))


def yoy_pct(amount: float, prev_amount: float) -> float:
    """전기대비 증감률(%) = (amount - prev) / prev * 100. 소수 1자리 반올림.

    prev_amount 가 0이면 계산 불가이므로 ValueError.
    """
    if prev_amount == 0:
        raise ValueError("전기 금액이 0이라 증감률(yoy_pct)을 계산할 수 없습니다.")
    return round_pct((amount - prev_amount) / prev_amount * 100)
