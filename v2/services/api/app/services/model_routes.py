from __future__ import annotations

import datetime as dt
from sqlalchemy.orm import Session

from listening_v2_shared.models import ModelRoute
from listening_v2_shared.model_routes import ensure_default_model_routes


def patch_model_routes(db: Session, items: list[dict]) -> list[ModelRoute]:
    ensure_default_model_routes(db)
    now = dt.datetime.now(dt.timezone.utc)
    updated: list[ModelRoute] = []
    for item in items:
        model_name = str(item.get('model_name') or '').strip()
        if not model_name:
            continue
        row = db.get(ModelRoute, model_name)
        if row is None:
            row = ModelRoute(model_name=model_name)
            db.add(row)
        row.enabled = bool(item.get('enabled'))
        row.cost_per_unit = float(item.get('cost_per_unit') or 0)
        row.multiplier = float(item.get('multiplier') or 1)
        row.updated_at = now
        updated.append(row)
    db.flush()
    return updated
