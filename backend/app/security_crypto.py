from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


_ENC_PREFIX = "enc:v1:"
_DEFAULT_DEV_MASTER_KEY = "dev-insecure-master-key-change-me"


def _derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(str(secret or "").encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_cipher() -> Fernet:
    master = str(os.getenv("APP_MASTER_KEY") or "").strip()
    if not master:
        master = str(os.getenv("APP_MASTER_KEY_FALLBACK") or _DEFAULT_DEV_MASTER_KEY).strip()
    return Fernet(_derive_fernet_key(master))


def encrypt_secret(value: str) -> str:
    plain = str(value or "")
    if not plain:
        return ""
    if plain.startswith(_ENC_PREFIX):
        return plain
    token = _get_cipher().encrypt(plain.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if not text.startswith(_ENC_PREFIX):
        return text
    token = text[len(_ENC_PREFIX):]
    try:
        return _get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
    except Exception:
        return ""


def has_encrypted_secret(value: str) -> bool:
    return str(value or "").startswith(_ENC_PREFIX)


def mask_secret(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}{'*' * (len(text) - 5)}{text[-2:]}"
