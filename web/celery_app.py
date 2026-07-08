"""Celery 앱 (Phase 6) — Redis broker/result backend.

`BackgroundTasks` 는 서버 재시작 시 작업이 유실된다. 무거운 에이전트 실행(A1~3, A4)과 RAG 적재를
Celery Task 로 분리해 Redis 큐에 영속화한다. 워커 구동:

    celery -A web.celery_app worker -l info

브로커/백엔드 URL 은 `REDIS_URL`(기본 redis://localhost:6379/0). 테스트는 `CELERY_TASK_ALWAYS_EAGER=1`
로 인프로세스 동기 실행이 가능하다(단, 태스크가 실 DB/LLM 을 물므로 통합 검증은 run_real_e2e 로 한다).
"""

from __future__ import annotations

import os

from celery import Celery

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "consult",
    broker=_REDIS_URL,
    backend=_REDIS_URL,
    include=["web.tasks"],  # 지연 임포트로 태스크 등록(순환 방지)
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_always_eager=os.environ.get("CELERY_TASK_ALWAYS_EAGER", "").lower() in ("1", "true", "yes"),
    task_eager_propagates=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)
