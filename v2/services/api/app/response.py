from __future__ import annotations

from typing import Any


def ok(*, request_id: str, data: Any, message: str = 'ok') -> dict:
    return {
        'requestId': request_id,
        'code': 'ok',
        'message': message,
        'data': data,
    }


def fail(*, request_id: str, code: str, message: str, data: Any = None) -> dict:
    return {
        'requestId': request_id,
        'code': code,
        'message': message,
        'data': data,
    }
