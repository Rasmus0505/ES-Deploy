from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from listening_v2_shared.models import Session as UserSession
from listening_v2_shared.models import User, WalletAccount

from ..db import session_scope
from ..deps import get_current_user
from ..response import ok
from ..schemas import LoginRequest, RegisterRequest
from ..security import create_access_token, decode_token, hash_password, verify_password

router = APIRouter(prefix='/api/v2/auth', tags=['auth'])


@router.post('/register')
def register(payload: RegisterRequest, request: Request):
    with session_scope() as db:
        exists = db.query(User).filter(User.email == payload.email.lower()).first()
        if exists is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='email_exists')

        user = User(email=payload.email.lower(), password_hash=hash_password(payload.password))
        db.add(user)
        db.flush()
        db.add(WalletAccount(user_id=user.id, balance_credits=0))
        token = create_access_token(user_id=user.id, db=db)
        db.flush()
        return ok(
            request_id=request.state.request_id,
            data={
                'token': token,
                'user': {'id': user.id, 'email': user.email},
            },
            message='registered',
        )


@router.post('/login')
def login(payload: LoginRequest, request: Request):
    with session_scope() as db:
        user = db.query(User).filter(User.email == payload.email.lower()).first()
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid_credentials')
        token = create_access_token(user_id=user.id, db=db)
        db.flush()
        return ok(
            request_id=request.state.request_id,
            data={
                'token': token,
                'user': {'id': user.id, 'email': user.email},
            },
            message='logged_in',
        )


@router.get('/me')
def me(request: Request, user: User = Depends(get_current_user)):
    return ok(request_id=request.state.request_id, data={'id': user.id, 'email': user.email})


@router.post('/logout')
def logout(request: Request, user: User = Depends(get_current_user)):
    auth_header = str(request.headers.get('authorization') or '').strip()
    if not auth_header.lower().startswith('bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing_token')
    token = auth_header[7:].strip()
    payload = decode_token(token)
    jti = str(payload.get('jti') or '').strip()
    if not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid_token')
    with session_scope() as db:
        row = (
            db.query(UserSession)
            .filter(UserSession.user_id == user.id, UserSession.token_jti == jti, UserSession.revoked_at.is_(None))
            .first()
        )
        if row is not None:
            import datetime as dt

            row.revoked_at = dt.datetime.now(dt.timezone.utc)
            db.flush()
    return ok(request_id=request.state.request_id, data={'loggedOut': True}, message='logged_out')
