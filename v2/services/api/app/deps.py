from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from listening_v2_shared.models import Session as UserSession
from listening_v2_shared.models import User

from .db import session_scope
from .security import decode_token


auth_scheme = HTTPBearer(auto_error=False)


def get_request_id(request: Request) -> str:
    return str(getattr(request.state, 'request_id', ''))


def get_current_user(credentials: HTTPAuthorizationCredentials = auth_scheme) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='missing_token')
    token = credentials.credentials
    payload = decode_token(token)
    user_id = str(payload.get('sub') or '').strip()
    jti = str(payload.get('jti') or '').strip()
    if not user_id or not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid_token')

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='user_not_found')
        session_row = db.query(UserSession).filter(UserSession.token_jti == jti, UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).first()
        if session_row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='session_revoked')
        return user


def require_admin(request: Request) -> None:
    from listening_v2_shared.config import get_settings

    token = str(request.headers.get('x-admin-token') or '').strip()
    if token != get_settings().admin_api_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='admin_forbidden')
