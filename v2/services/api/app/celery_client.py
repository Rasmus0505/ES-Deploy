from celery import Celery

from listening_v2_shared.config import get_settings

settings = get_settings()
celery_app = Celery('listening_v2_api', broker=settings.redis_url, backend=settings.redis_url)


def enqueue_pipeline(job_id: str) -> None:
    celery_app.send_task('listening_v2_worker.run_pipeline', args=[job_id])
