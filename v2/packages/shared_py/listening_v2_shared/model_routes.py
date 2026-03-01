from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ModelRoute


DEFAULT_ROUTES = [
    {'model_name': 'paraformer-v2', 'enabled': True, 'cost_per_unit': 0.25, 'multiplier': 1.0},
    {'model_name': 'qwen3-asr-flash', 'enabled': True, 'cost_per_unit': 0.35, 'multiplier': 1.0},
    {'model_name': 'qwen-mt', 'enabled': True, 'cost_per_unit': 2.0, 'multiplier': 1.0},
]


def ensure_default_model_routes(db: Session) -> None:
    existing = {row.model_name for row in db.execute(select(ModelRoute)).scalars().all()}
    for row in DEFAULT_ROUTES:
        if row['model_name'] in existing:
            continue
        db.add(ModelRoute(**row))
    db.commit()


def get_model_route(db: Session, model_name: str) -> ModelRoute:
    row = db.get(ModelRoute, model_name)
    if row is None:
        row = ModelRoute(model_name=model_name, enabled=True, cost_per_unit=0.0, multiplier=1.0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row
