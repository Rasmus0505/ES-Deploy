from __future__ import annotations

import datetime as dt
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from listening_v2_shared.config import get_settings
from listening_v2_shared.models import Session as UserSession


pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(*, user_id: str, db: Session) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(minutes=settings.jwt_exp_minutes)
    jti = uuid.uuid4().hex
    payload = {'sub': user_id, 'jti': jti, 'iat': int(now.timestamp()), 'exp': int(exp.timestamp())}
    token = jwt.encode(payload, settings.jwt_secret, algorithm='HS256')
    db.add(UserSession(user_id=user_id, token_jti=jti))
    return token


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=['HS256'])
    except JWTError as exc:
        raise ValueError('invalid_token') from exc
