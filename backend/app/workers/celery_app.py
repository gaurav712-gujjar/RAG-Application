from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("docchat", broker=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # re-deliver if a worker dies mid-task (ingestion is idempotent)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # long tasks: don't hoard jobs
    task_time_limit=900,
    task_soft_time_limit=840,
    broker_connection_retry_on_startup=True,
    task_ignore_result=True,
)
