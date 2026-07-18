from celery import Celery

from settings import get_settings


settings = get_settings()

celery_app = Celery("yield_agent", broker=settings.redis_url, backend=None)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=1740,
    task_time_limit=1800,
    broker_transport_options={"visibility_timeout": 2100},
    task_default_queue="analysis",
)
