from __future__ import annotations

import datetime as dt
import math
import subprocess
import time
from pathlib import Path

from celery.utils.log import get_task_logger
from prometheus_client import Counter, Histogram
from sqlalchemy.orm import Session

from listening_v2_shared.config import get_settings
from listening_v2_shared.db import SessionLocal, init_db
from listening_v2_shared.model_routes import get_model_route
from listening_v2_shared.models import AsrSegment, ExerciseItem, ExerciseSet, MediaAsset, ProcessingJob, WalletAccount, WalletLedger
from listening_v2_shared.oss_storage import delete_object, upload_file

from .exercise_builder import build_item_payload, check_needs_review
from .model_client import transcribe_audio, translate_to_zh
from .worker_app import worker_app

logger = get_task_logger(__name__)
settings = get_settings()
PIPELINE_TOTAL = Counter('listening_v2_worker_pipeline_total', 'Pipeline task executions', ['status'])
PIPELINE_DURATION = Histogram('listening_v2_worker_pipeline_duration_seconds', 'Pipeline duration seconds')
PIPELINE_SEGMENTS = Counter('listening_v2_worker_segments_total', 'Generated sentence segments')


def _db() -> Session:
    return SessionLocal()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _runtime_root() -> Path:
    root = Path(settings.runtime_dir).resolve() / 'worker_runtime'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _set_job_state(db: Session, job: ProcessingJob, *, status: str, stage: str, progress: int, error_code: str = '', error_message: str = '') -> None:
    job.status = status
    job.current_stage = stage
    job.progress_percent = max(0, min(100, int(progress)))
    job.error_code = error_code
    job.error_message = error_message
    if status in {'succeeded', 'failed', 'cancelled'}:
        job.completed_at = _now()
    job.updated_at = _now()
    db.flush()


def _ensure_not_cancelled(db: Session, job_id: str) -> None:
    row = db.get(ProcessingJob, job_id)
    if row is not None and row.status == 'cancelled':
        raise RuntimeError('cancelled')


def _download_from_url(source_url: str, output_dir: Path) -> str:
    output_path = output_dir / 'downloaded.mp4'
    cmd = ['yt-dlp', '-o', str(output_path), source_url]
    subprocess.run(cmd, check=True, capture_output=True)
    if not output_path.exists():
        raise RuntimeError('download_failed')
    return str(output_path)


def _extract_audio(video_path: str, output_dir: Path) -> str:
    audio_path = output_dir / 'audio.wav'
    cmd = ['ffmpeg', '-y', '-i', video_path, '-ac', '1', '-ar', '16000', str(audio_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    if not audio_path.exists():
        raise RuntimeError('audio_extract_failed')
    return str(audio_path)


def _extract_clip(audio_path: str, output_dir: Path, *, segment_index: int, start_ms: int, end_ms: int) -> str:
    clip_dir = output_dir / 'clips'
    clip_dir.mkdir(parents=True, exist_ok=True)
    target = clip_dir / f'segment_{segment_index:04d}.mp3'
    start_sec = max(0.0, float(start_ms) / 1000.0)
    duration_sec = max(0.5, (float(end_ms) - float(start_ms)) / 1000.0)
    cmd = [
        'ffmpeg',
        '-y',
        '-ss',
        f'{start_sec:.3f}',
        '-t',
        f'{duration_sec:.3f}',
        '-i',
        audio_path,
        '-codec:a',
        'libmp3lame',
        '-q:a',
        '4',
        str(target),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    if not target.exists():
        raise RuntimeError('clip_extract_failed')
    return str(target)


def _probe_duration_seconds(media_path: str) -> float:
    cmd = [
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        media_path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return max(0.0, float((out.stdout or '0').strip() or 0.0))


def _wallet_consume(db: Session, *, user_id: str, amount: int, idempotency_key: str, metadata: dict) -> WalletLedger:
    wallet = db.get(WalletAccount, user_id)
    if wallet is None:
        wallet = WalletAccount(user_id=user_id, balance_credits=0)
        db.add(wallet)
        db.flush()

    existing = db.query(WalletLedger).filter(WalletLedger.idempotency_key == idempotency_key).first()
    if existing:
        return existing

    if int(wallet.balance_credits) < int(amount):
        raise ValueError('insufficient_credits')

    wallet.balance_credits = int(wallet.balance_credits) - int(amount)
    row = WalletLedger(
        user_id=user_id,
        entry_type='consume',
        delta_credits=-int(amount),
        balance_after=int(wallet.balance_credits),
        idempotency_key=idempotency_key,
        description='job_consume',
        metadata_json=metadata,
    )
    db.add(row)
    db.flush()
    return row


@worker_app.task(name='listening_v2_worker.run_pipeline')
def run_pipeline(job_id: str) -> None:
    if settings.auto_init_db:
        init_db()
    started = time.perf_counter()
    db = _db()
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            logger.error('[DEBUG] job missing: %s', job_id)
            return

        media = db.get(MediaAsset, job.media_asset_id) if job.media_asset_id else None
        if media is None:
            _set_job_state(db, job, status='failed', stage='ingest', progress=100, error_code='media_not_found', error_message='media missing')
            db.commit()
            return

        runtime = _runtime_root() / job_id
        runtime.mkdir(parents=True, exist_ok=True)
        _set_job_state(db, job, status='running', stage='ingest', progress=5)
        db.commit()
        _ensure_not_cancelled(db, job_id)

        video_path = str(media.local_path or '').strip()
        if media.source_type == 'url':
            _set_job_state(db, job, status='running', stage='fetch', progress=10)
            db.commit()
            _ensure_not_cancelled(db, job_id)
            video_path = _download_from_url(media.source_url, runtime)
            media.local_path = video_path
            db.flush()

        if not video_path or not Path(video_path).exists():
            raise RuntimeError('input_media_missing')

        if not media.object_key:
            media.object_key = upload_file(video_path, object_prefix='source')

        _set_job_state(db, job, status='running', stage='audio_extract', progress=20)
        db.commit()
        _ensure_not_cancelled(db, job_id)
        audio_path = _extract_audio(video_path, runtime)
        duration = _probe_duration_seconds(video_path)
        media.duration_seconds = duration

        if duration > settings.max_video_minutes * 60:
            raise RuntimeError('video_too_long')

        asr_route = get_model_route(db, job.model_asr)
        mt_route = get_model_route(db, job.model_mt)
        if not bool(asr_route.enabled):
            raise RuntimeError('asr_model_disabled')
        if not bool(mt_route.enabled):
            raise RuntimeError('mt_model_disabled')

        _set_job_state(db, job, status='running', stage='asr', progress=35)
        db.commit()
        _ensure_not_cancelled(db, job_id)
        segments = transcribe_audio(audio_path, job.model_asr)
        if not segments:
            raise RuntimeError('asr_empty')

        _set_job_state(db, job, status='running', stage='translate', progress=55)
        db.commit()
        _ensure_not_cancelled(db, job_id)
        translated_segments: list[dict] = []
        for idx, seg in enumerate(segments):
            _ensure_not_cancelled(db, job_id)
            zh = translate_to_zh(str(seg.get('text') or ''), job.model_mt)
            translated_segments.append(
                {
                    'idx': idx,
                    'start_ms': int(seg.get('start_ms') or 0),
                    'end_ms': int(seg.get('end_ms') or 0),
                    'text': str(seg.get('text') or '').strip(),
                    'translation': zh,
                    'needs_review': check_needs_review(int(seg.get('start_ms') or 0), int(seg.get('end_ms') or 0)),
                }
            )

        asr_cost = float(duration) * float(asr_route.cost_per_unit) * float(asr_route.multiplier)
        mt_cost = float(len(translated_segments)) * float(mt_route.cost_per_unit) * float(mt_route.multiplier)
        cost = int(math.ceil(max(0.0, asr_cost + mt_cost)))
        _wallet_consume(
            db,
            user_id=job.user_id,
            amount=cost,
            idempotency_key=f'job:{job.job_id}:consume',
            metadata={'jobId': job.job_id, 'duration': duration, 'segments': len(translated_segments)},
        )

        _set_job_state(db, job, status='running', stage='exercise_pack', progress=80)
        db.commit()
        _ensure_not_cancelled(db, job_id)

        exercise_set = ExerciseSet(user_id=job.user_id, job_id=job.job_id, title=f'Exercise {job.job_id[:8]}')
        db.add(exercise_set)
        db.flush()

        for seg in translated_segments:
            asr_row = AsrSegment(
                job_id=job.job_id,
                segment_index=seg['idx'],
                start_ms=seg['start_ms'],
                end_ms=seg['end_ms'],
                transcript_en=seg['text'],
                translation_zh=seg['translation'],
                needs_review=seg['needs_review'],
            )
            db.add(asr_row)

            words, accepted = build_item_payload(text=seg['text'])
            clip_local_path = ''
            clip_object_key = ''
            try:
                clip_local_path = _extract_clip(
                    audio_path,
                    runtime,
                    segment_index=int(seg['idx']),
                    start_ms=int(seg['start_ms']),
                    end_ms=int(seg['end_ms']),
                )
                clip_object_key = upload_file(clip_local_path, object_prefix='clips')
            except Exception:
                clip_local_path = ''
                clip_object_key = ''
            item = ExerciseItem(
                exercise_set_id=exercise_set.id,
                segment_index=seg['idx'],
                start_ms=seg['start_ms'],
                end_ms=seg['end_ms'],
                transcript_en=seg['text'],
                translation_zh=seg['translation'],
                audio_local_path=clip_local_path,
                audio_clip_key=clip_object_key,
                words_json=words,
                accepted_json=accepted,
            )
            db.add(item)

        media.expires_at = _now() + dt.timedelta(hours=settings.keep_source_hours)
        job.exercise_set_id = exercise_set.id
        _set_job_state(db, job, status='succeeded', stage='completed', progress=100)
        db.commit()
        PIPELINE_TOTAL.labels(status='succeeded').inc()
        PIPELINE_SEGMENTS.inc(len(translated_segments))
        logger.info('[DEBUG] pipeline done job_id=%s exercise_set_id=%s', job.job_id, exercise_set.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job is not None:
            raw_error = str(exc)
            error_code = raw_error if raw_error else 'pipeline_failed'
            error_message = raw_error
            status = 'failed'
            if raw_error == 'cancelled':
                status = 'cancelled'
                error_code = ''
                error_message = 'cancelled by user'
            if raw_error == 'download_failed':
                error_message = '链接下载失败，请改为上传视频文件后重试'
            elif raw_error == 'video_too_long':
                error_message = f'视频超过 {settings.max_video_minutes} 分钟限制，请裁剪后重试'
            _set_job_state(
                db,
                job,
                status=status,
                stage=job.current_stage or 'pipeline',
                progress=100,
                error_code=error_code,
                error_message=error_message,
            )
            db.commit()
        PIPELINE_TOTAL.labels(status='failed' if str(exc) != 'cancelled' else 'cancelled').inc()
        logger.exception('[DEBUG] pipeline failed job_id=%s error=%s', job_id, exc)
    finally:
        PIPELINE_DURATION.observe(max(0.0, time.perf_counter() - started))
        db.close()


@worker_app.task(name='listening_v2_worker.cleanup_expired_media')
def cleanup_expired_media() -> dict:
    if settings.auto_init_db:
        init_db()
    db = _db()
    removed = 0
    try:
        now = _now()
        rows = db.query(MediaAsset).filter(MediaAsset.deleted_at.is_(None), MediaAsset.expires_at.is_not(None), MediaAsset.expires_at < now).all()
        for row in rows:
            if row.local_path and Path(row.local_path).exists():
                try:
                    Path(row.local_path).unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    pass
            if row.object_key:
                try:
                    delete_object(row.object_key)
                except Exception:
                    pass
            row.deleted_at = now
        clip_cutoff = now - dt.timedelta(hours=settings.keep_intermediate_hours)
        stale_items = (
            db.query(ExerciseItem)
            .filter(ExerciseItem.created_at < clip_cutoff)
            .filter((ExerciseItem.audio_local_path != '') | (ExerciseItem.audio_clip_key != ''))
            .all()
        )
        for item in stale_items:
            if item.audio_local_path and Path(item.audio_local_path).exists():
                try:
                    Path(item.audio_local_path).unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    pass
            if item.audio_clip_key:
                try:
                    delete_object(item.audio_clip_key)
                except Exception:
                    pass
            item.audio_local_path = ''
            item.audio_clip_key = ''
        db.commit()
        return {'removed': removed}
    finally:
        db.close()
