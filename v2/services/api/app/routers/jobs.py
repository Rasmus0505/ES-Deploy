from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from listening_v2_shared.config import get_settings
from listening_v2_shared.model_routes import get_model_route
from listening_v2_shared.models import MediaAsset, ProcessingJob, User

from ..db import session_scope
from ..deps import get_current_user
from ..response import ok
from ..services.wallet import get_or_create_wallet

try:
    from ..celery_client import enqueue_pipeline
except Exception:  # pragma: no cover
    enqueue_pipeline = None

router = APIRouter(prefix='/api/v2/jobs', tags=['jobs'])
settings = get_settings()


def _ensure_runtime() -> Path:
    root = Path(settings.runtime_dir).resolve() / 'api_uploads'
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post('')
async def create_job(
    request: Request,
    source_url: str = Form(default=''),
    asr_model: str = Form(default='paraformer-v2'),
    mt_model: str = Form(default='qwen-mt'),
    video_file: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
):
    source_url = str(source_url or '').strip()
    source_type = 'url' if source_url else 'file'
    if source_type == 'file' and video_file is None:
        raise HTTPException(status_code=400, detail='video_file_or_url_required')
    if source_type == 'url' and not (source_url.startswith('http://') or source_url.startswith('https://')):
        raise HTTPException(status_code=400, detail='invalid_source_url')

    with session_scope() as db:
        wallet = get_or_create_wallet(db, user.id)
        if int(wallet.balance_credits) <= 0:
            raise HTTPException(status_code=402, detail='insufficient_credits')

        asr_route = get_model_route(db, asr_model)
        mt_route = get_model_route(db, mt_model)
        if not bool(asr_route.enabled):
            raise HTTPException(status_code=403, detail='asr_model_disabled')
        if not bool(mt_route.enabled):
            raise HTTPException(status_code=403, detail='mt_model_disabled')

        local_path = ''
        if source_type == 'file' and video_file is not None:
            suffix = Path(video_file.filename or 'video.mp4').suffix or '.mp4'
            media_dir = _ensure_runtime() / uuid.uuid4().hex
            media_dir.mkdir(parents=True, exist_ok=True)
            local_path = str(media_dir / f'input{suffix}')
            with open(local_path, 'wb') as out:
                while True:
                    chunk = await video_file.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

        media = MediaAsset(
            user_id=user.id,
            source_type=source_type,
            source_url=source_url,
            local_path=local_path,
            created_at=dt.datetime.now(dt.timezone.utc),
            expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=settings.keep_source_hours),
        )
        db.add(media)
        db.flush()

        job = ProcessingJob(
            user_id=user.id,
            media_asset_id=media.id,
            status='queued',
            progress_percent=0,
            current_stage='queued',
            model_asr=asr_model,
            model_mt=mt_model,
            idempotency_key=f'job:{uuid.uuid4().hex}',
        )
        db.add(job)
        db.flush()

        if enqueue_pipeline is not None:
            enqueue_pipeline(job.job_id)

        return ok(
            request_id=request.state.request_id,
            data={
                'jobId': job.job_id,
                'status': job.status,
            },
            message='queued',
        )


@router.get('/{job_id}')
def get_job(job_id: str, request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        row = db.get(ProcessingJob, job_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail='job_not_found')
        return ok(
            request_id=request.state.request_id,
            data={
                'jobId': row.job_id,
                'status': row.status,
                'progressPercent': int(row.progress_percent),
                'stage': row.current_stage,
                'errorCode': row.error_code,
                'errorMessage': row.error_message,
                'exerciseSetId': row.exercise_set_id,
                'updatedAt': row.updated_at.isoformat(),
            },
        )


@router.post('/{job_id}/retry')
def retry_job(job_id: str, request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        row = db.get(ProcessingJob, job_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail='job_not_found')
        if row.status not in {'failed', 'cancelled'}:
            raise HTTPException(status_code=409, detail='job_not_retryable')
        row.status = 'queued'
        row.progress_percent = 0
        row.current_stage = 'queued'
        row.error_code = ''
        row.error_message = ''
        row.queue_attempts = int(row.queue_attempts or 0) + 1
        db.flush()
        if enqueue_pipeline is not None:
            enqueue_pipeline(row.job_id)
        return ok(
            request_id=request.state.request_id,
            data={
                'jobId': row.job_id,
                'status': row.status,
                'queueAttempts': int(row.queue_attempts),
            },
            message='requeued',
        )


@router.post('/{job_id}/cancel')
def cancel_job(job_id: str, request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        row = db.get(ProcessingJob, job_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail='job_not_found')
        if row.status in {'succeeded', 'failed', 'cancelled'}:
            raise HTTPException(status_code=409, detail='job_not_cancellable')
        row.status = 'cancelled'
        row.current_stage = 'cancelled'
        row.progress_percent = 100
        row.error_code = ''
        row.error_message = 'cancelled by user'
        row.completed_at = dt.datetime.now(dt.timezone.utc)
        db.flush()
        return ok(
            request_id=request.state.request_id,
            data={'jobId': row.job_id, 'status': row.status},
            message='cancelled',
        )
