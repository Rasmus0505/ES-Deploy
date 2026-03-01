from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from listening_v2_shared.models import WalletAccount, WalletLedger


def get_or_create_wallet(db: Session, user_id: str) -> WalletAccount:
    wallet = db.get(WalletAccount, user_id)
    if wallet is None:
        wallet = WalletAccount(user_id=user_id, balance_credits=0)
        db.add(wallet)
        db.flush()
    return wallet


def append_ledger(
    db: Session,
    *,
    user_id: str,
    entry_type: str,
    delta_credits: int,
    idempotency_key: str,
    description: str,
    metadata_json: dict[str, Any] | None = None,
) -> WalletLedger:
    wallet = get_or_create_wallet(db, user_id)
    existing = db.query(WalletLedger).filter(WalletLedger.idempotency_key == idempotency_key).first()
    if existing is not None:
        return existing

    next_balance = int(wallet.balance_credits) + int(delta_credits)
    if next_balance < 0:
        raise ValueError('insufficient_credits')

    wallet.balance_credits = next_balance
    wallet.updated_at = dt.datetime.now(dt.timezone.utc)
    ledger = WalletLedger(
        user_id=user_id,
        entry_type=entry_type,
        delta_credits=int(delta_credits),
        balance_after=next_balance,
        idempotency_key=idempotency_key,
        description=description,
        metadata_json=metadata_json or {},
    )
    db.add(ledger)
    db.flush()
    return ledger


def list_ledger(db: Session, user_id: str, limit: int = 100) -> list[WalletLedger]:
    return db.query(WalletLedger).filter(WalletLedger.user_id == user_id).order_by(desc(WalletLedger.created_at)).limit(limit).all()
