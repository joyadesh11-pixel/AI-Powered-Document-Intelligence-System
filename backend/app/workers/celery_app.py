"""Celery application.

Run from backend/ (Windows has no prefork pool, so use threads or solo):

    celery -A app.workers.celery_app worker --pool=threads --concurrency=4 --loglevel=info
    celery -A app.workers.celery_app worker --pool=solo --loglevel=info     # debugging
    celery -A app.workers.celery_app beat --loglevel=info                   # separate process

Notes:
- No result backend: task outcomes live in the database, not in Redis.
- acks_late + reject_on_worker_lost: a task is re-delivered if its worker dies;
  processing.claim() makes the redelivery harmless.
- The threads pool cannot enforce time limits; the limits below apply on
  prefork/Linux. Stuck `processing` rows are reclaimed by claim() after
  TASK_TIME_LIMIT_S either way.
"""
from __future__ import annotations

from celery import Celery

from app.pipeline.config import get_config

_cfg = get_config()

celery_app = Celery("dociq", broker=_cfg.REDIS_URL, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=_cfg.TASK_TIME_LIMIT_S,
    task_soft_time_limit=_cfg.TASK_SOFT_TIME_LIMIT_S,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_always_eager=_cfg.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=True,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "retry-unindexed": {"task": "retry_unindexed", "schedule": 300},
    },
)
