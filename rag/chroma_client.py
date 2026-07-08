"""CaseStore 추상화 & 코호트 조회 (CLAUDE.md §2.4, SPEC §1.4).

`past_consulting_cases` 저장/조회를 추상화한다. 오케스트레이터의 `StateStore` 패턴과 동일하게
Protocol + 인메모리/실제 구현으로 분리해, 테스트는 chromadb 없이 전 로직을 검증한다.

- `CaseStore`(Protocol): upsert/query 계약.
- `InMemoryCaseStore`: dict 저장, 코호트 메타 완전일치 필터 + 텍스트 유사도(difflib) 스텁.
- `ChromaCaseStore`: chromadb 지연 임포트(미설치 시 명확한 에러).

**코호트 필터 + 완화 Fallback**(`retrieve_cases`): 업종+상권+성별+연령대+성향 완전 코호트로
조회하되, 결과가 부족하면 `relaxation_order`(성향→성별→연령대) 순으로 조건을 단계적으로 해제한다.
**업종·상권(district_type)은 절대 완화하지 않는다** — 이종 상권/타겟 오염 방지(`category_mismatch`).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Protocol

# 완화 순서(§2.4): 성향 → 성별 → 연령대. industry·district_type 은 항상 유지.
DEFAULT_RELAXATION_ORDER: tuple[str, ...] = (
    "risk_appetite",
    "owner_gender",
    "owner_age_band",
)
# 완화하지 않는 핵심 코호트 키(오염 방지).
_CORE_COHORT_KEYS: tuple[str, ...] = ("industry", "district_type")


class CaseStore(Protocol):
    """past_consulting_cases 저장/조회 계약."""

    def upsert(self, case_id: str, document: str, metadata: dict[str, Any]) -> None:
        """케이스를 임베딩 문서 + 코호트 메타와 함께 upsert(멱등)."""
        ...

    def query(
        self, where: dict[str, Any], query_text: str, top_k: int
    ) -> list[dict[str, Any]]:
        """코호트 메타 필터(where) + 유사도로 조회.

        반환: [{case_id, metadata, document, similarity(0~1)}] — similarity 내림차순.
        """
        ...


class InMemoryCaseStore:
    """테스트/개발용 인메모리 CaseStore(chromadb 불필요).

    `where` 는 메타데이터 완전일치 필터. similarity 는 query_text↔document 의 difflib 비율
    스텁(빈 query_text 는 1.0). 실제 임베딩 유사도는 ChromaCaseStore 가 담당.
    """

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def upsert(self, case_id: str, document: str, metadata: dict[str, Any]) -> None:
        self._records[case_id] = {"document": document, "metadata": dict(metadata)}

    @staticmethod
    def _matches(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
        return all(metadata.get(k) == v for k, v in where.items())

    @staticmethod
    def _similarity(query_text: str, document: str) -> float:
        if not query_text:
            return 1.0
        return SequenceMatcher(None, query_text, document).ratio()

    def query(
        self, where: dict[str, Any], query_text: str, top_k: int
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for case_id, rec in self._records.items():
            if not self._matches(rec["metadata"], where):
                continue
            hits.append(
                {
                    "case_id": case_id,
                    "metadata": rec["metadata"],
                    "document": rec["document"],
                    "similarity": self._similarity(query_text, rec["document"]),
                }
            )
        hits.sort(key=lambda h: h["similarity"], reverse=True)
        return hits[:top_k]


class ChromaCaseStore:
    """실제 ChromaDB 백엔드(지연 임포트). chromadb 미설치 시 명확한 에러."""

    def __init__(self, collection_name: str = "past_consulting_cases", persist_dir: str | None = None):
        try:
            import chromadb  # 지연 임포트: 코어 설치엔 chromadb 불필요(optional extra 'rag').
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "chromadb 가 설치되지 않았습니다. `pip install '.[rag]'` 로 설치하세요."
            ) from exc
        client = (
            chromadb.PersistentClient(path=persist_dir) if persist_dir else chromadb.Client()
        )
        self._collection = client.get_or_create_collection(collection_name)

    def upsert(self, case_id: str, document: str, metadata: dict[str, Any]) -> None:
        self._collection.upsert(ids=[case_id], documents=[document], metadatas=[metadata])

    @staticmethod
    def _where_clause(where: dict[str, Any]) -> dict[str, Any]:
        """Chroma where 절 구성(다중 키는 $and)."""
        if len(where) <= 1:
            return dict(where)
        return {"$and": [{k: {"$eq": v}} for k, v in where.items()]}

    def query(
        self, where: dict[str, Any], query_text: str, top_k: int
    ) -> list[dict[str, Any]]:  # pragma: no cover - 실제 chromadb 필요
        res = self._collection.query(
            query_texts=[query_text or ""],
            n_results=top_k,
            where=self._where_clause(where) if where else None,
        )
        out: list[dict[str, Any]] = []
        ids = (res.get("ids") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, case_id in enumerate(ids):
            distance = dists[i] if i < len(dists) else 0.0
            out.append(
                {
                    "case_id": case_id,
                    "metadata": metas[i] if i < len(metas) else {},
                    "document": docs[i] if i < len(docs) else "",
                    "similarity": 1.0 / (1.0 + float(distance)),
                }
            )
        return out


def retrieve_cases(
    store: CaseStore,
    cohort: dict[str, Any],
    query_text: str,
    *,
    top_k: int = 3,
    min_similarity: float = 0.75,
    min_results: int = 1,
    relaxation_order: tuple[str, ...] = DEFAULT_RELAXATION_ORDER,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """코호트 필터 + 완화 Fallback 으로 과거 케이스를 조회한다.

    완전 코호트로 조회 → 임계값 통과 케이스가 `min_results` 미만이면 `relaxation_order` 순으로
    코호트 키를 단계적으로 해제하며 재조회한다(industry·district_type 은 항상 유지). 각 단계에서
    `min_similarity` 미달 케이스는 제외한다.

    Returns:
        (cases, relaxation_level, relaxed_fields) — cases 는 similarity 내림차순 top_k.
    """
    relaxed_fields: list[str] = []

    def _run(active_where: dict[str, Any]) -> list[dict[str, Any]]:
        hits = store.query(active_where, query_text, top_k)
        return [h for h in hits if h["similarity"] >= min_similarity]

    # level 0 — 완전 코호트(존재하는 키만).
    where = {k: cohort[k] for k in (*_CORE_COHORT_KEYS, *relaxation_order) if cohort.get(k) is not None}
    qualified = _run(where)

    # Fallback — 부족하면 relaxation_order 순으로 한 키씩 해제.
    for field in relaxation_order:
        if len(qualified) >= min_results:
            break
        if field in where:
            del where[field]
            relaxed_fields.append(field)
            qualified = _run(where)

    qualified.sort(key=lambda h: h["similarity"], reverse=True)
    return qualified[:top_k], len(relaxed_fields), relaxed_fields
