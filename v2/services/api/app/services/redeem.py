from __future__ import annotations

import datetime as dt
import secrets
import string

from sqlalchemy.orm import Session

from listening_v2_shared.models import RedeemCode


ALPHABET = string.ascii_uppercase + string.digits


def generate_code(prefix: str) -> str:
    body = ''.join(secrets.choice(ALPHABET) for _ in range(12))
    safe_prefix = ''.join(ch for ch in (prefix or 'V2').upper() if ch.isalnum())[:6]
    return f'{safe_prefix}{body}'


def create_redeem_codes(db: Session, *, count: int, credits: int, expires_days: int, prefix: str, created_by: str = 'admin') -> list[str]:
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=expires_days)
    codes: list[str] = []
    while len(codes) < count:
        code = generate_code(prefix)
        if db.get(RedeemCode, code) is not None:
            continue
        row = RedeemCode(code=code, credits=credits, created_by=created_by, expires_at=expires_at, status='active')
        db.add(row)
        codes.append(code)
    db.flush()
    return codes
