from contextlib import contextmanager

from sqlalchemy.orm import Session

from listening_v2_shared.db import SessionLocal


@contextmanager
def session_scope() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
