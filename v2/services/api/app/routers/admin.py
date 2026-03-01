from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from listening_v2_shared.models import ModelRoute

from ..db import session_scope
from ..deps import require_admin
from ..response import ok
from ..schemas import CreateRedeemCodesRequest, PatchModelRoutesRequest
from ..services.model_routes import patch_model_routes
from ..services.redeem import create_redeem_codes

router = APIRouter(prefix='/api/v2/admin', tags=['admin'])


@router.post('/redeem-codes')
def create_codes(payload: CreateRedeemCodesRequest, request: Request, _: None = Depends(require_admin)):
    with session_scope() as db:
        codes = create_redeem_codes(
            db,
            count=payload.count,
            credits=payload.credits,
            expires_days=payload.expires_days,
            prefix=payload.prefix,
            created_by='admin',
        )
        return ok(
            request_id=request.state.request_id,
            data={'codes': codes, 'count': len(codes)},
            message='created',
        )


@router.patch('/model-routes')
def patch_routes(payload: PatchModelRoutesRequest, request: Request, _: None = Depends(require_admin)):
    with session_scope() as db:
        rows = patch_model_routes(db, [item.model_dump() for item in payload.items])
        return ok(
            request_id=request.state.request_id,
            data={
                'items': [
                    {
                        'modelName': row.model_name,
                        'enabled': bool(row.enabled),
                        'costPerUnit': float(row.cost_per_unit),
                        'multiplier': float(row.multiplier),
                    }
                    for row in rows
                ]
            },
            message='patched',
        )


@router.get('/model-routes')
def list_routes(request: Request, _: None = Depends(require_admin)):
    with session_scope() as db:
        rows = db.query(ModelRoute).all()
        return ok(
            request_id=request.state.request_id,
            data={
                'items': [
                    {
                        'modelName': row.model_name,
                        'enabled': bool(row.enabled),
                        'costPerUnit': float(row.cost_per_unit),
                        'multiplier': float(row.multiplier),
                    }
                    for row in rows
                ]
            },
        )
