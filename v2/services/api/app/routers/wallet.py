from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc

from listening_v2_shared.models import RedeemCode, User, WalletAccount

from ..db import session_scope
from ..deps import get_current_user
from ..response import ok
from ..schemas import RedeemRequest
from ..services.wallet import append_ledger, get_or_create_wallet, list_ledger

router = APIRouter(prefix='/api/v2/wallet', tags=['wallet'])


@router.get('')
def get_wallet(request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        wallet = get_or_create_wallet(db, user.id)
        return ok(
            request_id=request.state.request_id,
            data={
                'balanceCredits': int(wallet.balance_credits),
            },
        )


@router.get('/ledger')
def get_wallet_ledger(request: Request, user: User = Depends(get_current_user)):
    with session_scope() as db:
        rows = list_ledger(db, user.id, limit=200)
        return ok(
            request_id=request.state.request_id,
            data=[
                {
                    'id': item.id,
                    'entryType': item.entry_type,
                    'deltaCredits': int(item.delta_credits),
                    'balanceAfter': int(item.balance_after),
                    'description': item.description,
                    'createdAt': item.created_at.isoformat(),
                }
                for item in rows
            ],
        )


@router.post('/redeem')
def redeem_code(payload: RedeemRequest, request: Request, user: User = Depends(get_current_user)):
    safe_code = payload.code.strip().upper()
    with session_scope() as db:
        code_row = db.get(RedeemCode, safe_code)
        if code_row is None:
            raise HTTPException(status_code=404, detail='redeem_code_not_found')
        if code_row.status != 'active':
            raise HTTPException(status_code=409, detail='redeem_code_unavailable')
        if code_row.expires_at and code_row.expires_at < dt.datetime.now(dt.timezone.utc):
            raise HTTPException(status_code=409, detail='redeem_code_expired')

        code_row.status = 'redeemed'
        code_row.redeemed_by = user.id
        code_row.redeemed_at = dt.datetime.now(dt.timezone.utc)

        try:
            ledger = append_ledger(
                db,
                user_id=user.id,
                entry_type='redeem',
                delta_credits=int(code_row.credits),
                idempotency_key=payload.idempotency_key,
                description=f'redeem:{safe_code}',
                metadata_json={'code': safe_code},
            )
        except ValueError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        wallet = get_or_create_wallet(db, user.id)
        db.flush()

        return ok(
            request_id=request.state.request_id,
            data={
                'code': safe_code,
                'balanceCredits': int(wallet.balance_credits),
                'ledgerId': ledger.id,
            },
            message='redeemed',
        )
