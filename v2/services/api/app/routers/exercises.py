from __future__ import annotations

import math
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import asc

from listening_v2_shared.models import ExerciseAttempt, ExerciseItem, ExerciseSet, LearningProgress, User
from listening_v2_shared.oss_storage import public_url

from ..db import session_scope
from ..deps import get_current_user
from ..response import ok
from ..schemas import SubmitAttemptRequest

router = APIRouter(prefix='/api/v2/exercises', tags=['exercises'])


def _normalize_words(words: list[str]) -> list[str]:
    return [str(item or '').strip().lower() for item in words if str(item or '').strip()]


@router.get('/{exercise_id}')
def get_exercise(exercise_id: str, request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        exercise = db.get(ExerciseSet, exercise_id)
        if exercise is None or exercise.user_id != user.id:
            raise HTTPException(status_code=404, detail='exercise_not_found')

        items = (
            db.query(ExerciseItem)
            .filter(ExerciseItem.exercise_set_id == exercise_id)
            .order_by(asc(ExerciseItem.segment_index))
            .all()
        )

        progress = (
            db.query(LearningProgress)
            .filter(LearningProgress.user_id == user.id, LearningProgress.exercise_set_id == exercise_id)
            .first()
        )

        return ok(
            request_id=request.state.request_id,
            data={
                'exerciseSet': {
                    'id': exercise.id,
                    'title': exercise.title,
                    'createdAt': exercise.created_at.isoformat(),
                },
                'progress': {
                    'lastSegmentIndex': int(progress.last_segment_index) if progress else 0,
                    'completedSegments': int(progress.completed_segments) if progress else 0,
                },
                'items': [
                    {
                        'id': item.id,
                        'segmentIndex': int(item.segment_index),
                        'startMs': int(item.start_ms),
                        'endMs': int(item.end_ms),
                        'transcriptEn': item.transcript_en,
                        'translationZh': item.translation_zh,
                        'wordCount': len(item.words_json or []),
                        'audioUrl': public_url(item.audio_clip_key) if item.audio_clip_key else f'/api/v2/exercises/items/{item.id}/audio',
                    }
                    for item in items
                ],
            },
        )


@router.get('/items/{item_id}/audio')
def get_item_audio(item_id: str):
    with session_scope() as db:
        item = db.get(ExerciseItem, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail='item_not_found')
        local_path = str(item.audio_local_path or '').strip()
        if not local_path:
            raise HTTPException(status_code=404, detail='audio_not_found')
        path = Path(local_path).resolve()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail='audio_not_found')
        return FileResponse(str(path), media_type='audio/mpeg', filename=f'{item_id}.mp3')


@router.post('/{exercise_id}/attempts')
def submit_attempt(
    exercise_id: str,
    payload: SubmitAttemptRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    with session_scope() as db:
        exercise = db.get(ExerciseSet, exercise_id)
        if exercise is None or exercise.user_id != user.id:
            raise HTTPException(status_code=404, detail='exercise_not_found')

        item = db.get(ExerciseItem, payload.item_id)
        if item is None or item.exercise_set_id != exercise_id:
            raise HTTPException(status_code=404, detail='item_not_found')

        expected_words = _normalize_words([str(x) for x in (item.accepted_json or item.words_json or [])])
        submitted_words = _normalize_words(payload.submitted_words)
        matches = 0
        total = max(1, len(expected_words))
        word_results: list[bool] = []
        for idx, expected in enumerate(expected_words):
            current = submitted_words[idx] if idx < len(submitted_words) else ''
            is_match = current == expected
            word_results.append(is_match)
            if is_match:
                matches += 1
        score = round((matches / total) * 100, 2)
        is_correct = score >= 100

        attempt = ExerciseAttempt(
            exercise_item_id=item.id,
            user_id=user.id,
            submitted_words_json=submitted_words,
            is_correct=is_correct,
            score=score,
        )
        db.add(attempt)

        progress = (
            db.query(LearningProgress)
            .filter(LearningProgress.user_id == user.id, LearningProgress.exercise_set_id == exercise_id)
            .first()
        )
        if progress is None:
            progress = LearningProgress(
                user_id=user.id,
                exercise_set_id=exercise_id,
                last_segment_index=int(item.segment_index),
                completed_segments=1 if is_correct else 0,
            )
            db.add(progress)
        else:
            progress.last_segment_index = max(int(progress.last_segment_index), int(item.segment_index))
            if is_correct:
                progress.completed_segments = int(progress.completed_segments) + 1

        db.flush()
        return ok(
            request_id=request.state.request_id,
            data={
                'attemptId': attempt.id,
                'score': score,
                'isCorrect': is_correct,
                'wordResults': word_results,
            },
            message='attempt_saved',
        )
