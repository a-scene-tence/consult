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
from celery.signals import after_setup_logger, after_setup_task_logger

from web.logging_config import configure_logging

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


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
    # 시한폭탄 방어 — Opus 응답 지연 대비 soft/hard 타임아웃(초).
    task_soft_time_limit=_int_env("CELERY_TASK_SOFT_TIME_LIMIT", 600),
    task_time_limit=_int_env("CELERY_TASK_TIME_LIMIT", 660),
)


# 워커 로그에도 구조적 로깅(cid/did) 포맷 적용.
@after_setup_logger.connect
def _setup_logger(**_kwargs) -> None:  # pragma: no cover - 워커 런타임
    configure_logging()


@after_setup_task_logger.connect
def _setup_task_logger(**_kwargs) -> None:  # pragma: no cover - 워커 런타임
    configure_logging()
