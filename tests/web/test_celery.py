"""Celery 태스크 등록·구성 검증 (Phase 6).

Redis 없이(브로커 미연결) 태스크가 등록되고 라우팅 이름이 맞는지, dispatch 토글(use_celery)이
환경변수로 동작하는지 검증한다. 실제 큐 실행(A1~4·RAG)은 실 DB/LLM 이 필요하므로 run_real_e2e 로 확인.
"""

from __future__ import annotations

import web.tasks  # noqa: F401 — 임포트로 태스크 등록
from web.celery_app import celery_app
from web.production import use_celery


def test_tasks_registered():
    names = set(celery_app.tasks.keys())
    assert {"consult.run_analysis", "consult.submit_feedback", "consult.index_case"} <= names


def test_task_backend_toggle(monkeypatch):
    monkeypatch.delenv("TASK_BACKEND", raising=False)
    assert use_celery() is False  # 기본 inline
    monkeypatch.setenv("TASK_BACKEND", "celery")
    assert use_celery() is True


def test_tasks_have_backoff_retry_policy():
    """전이성 Anthropic 오류에 대해 지수 백오프 재시도가 설정됐는지(브로커 불필요)."""
    import anthropic

    for name in ("run_analysis_task", "submit_feedback_task", "index_case_task"):
        task = getattr(web.tasks, name)
        assert task.retry_backoff is True
        assert task.max_retries >= 1
        assert anthropic.RateLimitError in task.autoretry_for      # 429
        assert anthropic.InternalServerError in task.autoretry_for  # 50x
        # 결정적 오류는 재시도 대상에서 제외되어야 한다.
        assert ValueError not in task.autoretry_for


def test_task_time_limits_configured():
    assert celery_app.conf.task_soft_time_limit and celery_app.conf.task_time_limit
    assert celery_app.conf.task_time_limit >= celery_app.conf.task_soft_time_limit


def test_index_fn_enqueues_when_celery(monkeypatch):
    """prod_index_fn 이 celery 모드에서 index_case_task 를 큐잉하는지(브로커 미연결 대체)."""
    monkeypatch.setenv("TASK_BACKEND", "celery")
    calls = []
    monkeypatch.setattr(web.tasks.index_case_task, "delay", lambda draft_id: calls.append(draft_id))
    from web.production import prod_index_fn

    prod_index_fn(case_store=object(), session_factory=None, draft_id=42)
    assert calls == [42]
