"""프로덕션 런타임 조립 (Phase 6) — 실제 Agent + DB + (선택) ChromaDB + Celery 디스패치.

웹 앱 기본값과 Celery 태스크가 **동일한 오케스트레이터 구성**을 공유하도록 팩토리를 모은다.
- `TASK_BACKEND=celery` 이면 무거운 작업을 Celery 큐로 디스패치(기본 inline: 테스트/개발).
- `RAG_ENABLED=1` 이면 ChromaCaseStore 를 물린다(기본 비활성 — chromadb 미설치 환경 허용).
"""

from __future__ import annotations

import os
from typing import Any

from orchestrator import Orchestrator, SqlAlchemyStore
from rag.case_indexer import index_published_case


def use_celery() -> bool:
    return os.environ.get("TASK_BACKEND", "inline").lower() == "celery"


def _rag_enabled() -> bool:
    return os.environ.get("RAG_ENABLED", "").lower() in ("1", "true", "yes")


def prod_case_store() -> Any | None:
    """운영 CaseStore(ChromaDB). RAG 비활성이면 None(적재/조회 건너뜀)."""
    if not _rag_enabled():
        return None
    from rag.chroma_client import ChromaCaseStore

    return ChromaCaseStore(
        collection_name=os.environ.get("CHROMA_COLLECTION", "past_consulting_cases"),
        persist_dir=os.environ.get("CHROMA_PERSIST_DIR"),
    )


def prod_index_fn(case_store: Any, session_factory: Any, draft_id: int, **ctx: Any) -> None:
    """RAG 적재 훅 — celery 백엔드면 큐로 분리, 아니면 인라인 실행."""
    if use_celery():
        from web.tasks import index_case_task

        index_case_task.delay(draft_id)
    else:
        index_published_case(case_store, session_factory, draft_id, **ctx)


def make_prod_orchestrator(store: SqlAlchemyStore) -> Orchestrator:
    """실제 Agent 1~4 + (선택) ChromaCaseStore + celery-aware 적재 훅으로 오케스트레이터 구성."""
    return Orchestrator(store, case_store=prod_case_store(), index_fn=prod_index_fn)
