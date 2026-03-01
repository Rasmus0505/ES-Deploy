from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from listening_v2_shared.models import DeletionRequest, ExerciseItem, ExerciseSet, MediaAsset, User
from listening_v2_shared.oss_storage import delete_object

from ..db import session_scope
from ..deps import get_current_user
from ..response import ok

router = APIRouter(prefix='/api/v2/me', tags=['me'])


@router.delete('/data')
def delete_my_data(request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        deletion = DeletionRequest(
            user_id=user.id,
            status='pending',
            note='user_requested',
        )
        db.add(deletion)
        db.flush()

        media_rows = db.query(MediaAsset).filter(MediaAsset.user_id == user.id).all()
        for media in media_rows:
            if media.local_path:
                try:
                    Path(media.local_path).unlink(missing_ok=True)
                except Exception:
                    pass
            if media.object_key:
                try:
                    delete_object(media.object_key)
                except Exception:
                    pass

        exercise_sets = db.query(ExerciseSet).filter(ExerciseSet.user_id == user.id).all()
        set_ids = [row.id for row in exercise_sets]
        if set_ids:
            items = db.query(ExerciseItem).filter(ExerciseItem.exercise_set_id.in_(set_ids)).all()
            for item in items:
                if item.audio_local_path:
                    try:
                        Path(item.audio_local_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                if item.audio_clip_key:
                    try:
                        delete_object(item.audio_clip_key)
                    except Exception:
                        pass

        target = db.get(User, user.id)
        if target is not None:
            db.delete(target)

        deletion.status = 'completed'
        deletion.finished_at = dt.datetime.now(dt.timezone.utc)
        db.flush()

        return ok(
            request_id=request.state.request_id,
            data={
                'deleted': True,
                'deletionRequestId': deletion.id,
            },
            message='deleted',
        )
