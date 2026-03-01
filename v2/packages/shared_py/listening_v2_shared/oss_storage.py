from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import oss2

from .config import get_settings

settings = get_settings()


def _build_bucket() -> oss2.Bucket | None:
    if not (settings.oss_access_key_id and settings.oss_access_key_secret and settings.oss_bucket and settings.oss_endpoint):
        return None
    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    endpoint = settings.oss_endpoint
    if not endpoint.startswith('http://') and not endpoint.startswith('https://'):
        endpoint = f'https://{endpoint}'
    return oss2.Bucket(auth, endpoint, settings.oss_bucket)


def upload_file(local_path: str, object_prefix: str = 'media') -> str:
    bucket = _build_bucket()
    if bucket is None:
        return ''
    target = Path(local_path)
    if not target.exists():
        return ''
    key = f"{object_prefix}/{dt.datetime.utcnow().strftime('%Y%m%d')}/{uuid.uuid4().hex}_{target.name}"
    bucket.put_object_from_file(key, str(target))
    return key


def delete_object(object_key: str) -> None:
    bucket = _build_bucket()
    if bucket is None or not object_key:
        return
    bucket.delete_object(object_key)


def public_url(object_key: str) -> str:
    if not object_key:
        return ''
    endpoint = settings.oss_endpoint
    endpoint = endpoint.replace('https://', '').replace('http://', '')
    return f'https://{settings.oss_bucket}.{endpoint}/{object_key}'
