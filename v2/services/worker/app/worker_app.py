from celery import Celery
from celery.schedules import crontab
from prometheus_client import start_http_server

from listening_v2_shared.config import get_settings

settings = get_settings()
worker_app = Celery('listening_v2_worker', broker=settings.redis_url, backend=settings.redis_url)
worker_app.conf.task_routes = {'listening_v2_worker.run_pipeline': {'queue': 'pipeline'}}
worker_app.conf.beat_schedule = {
    'cleanup-expired-media-every-30min': {
        'task': 'listening_v2_worker.cleanup_expired_media',
        'schedule': crontab(minute='*/30'),
    }
}

if settings.enable_metrics:
    try:
        start_http_server(settings.worker_metrics_port)
    except Exception:
        pass

import app.tasks  # noqa: E402,F401
