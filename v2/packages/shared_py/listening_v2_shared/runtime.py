from __future__ import annotations

import os
from pathlib import Path


def ensure_runtime_dir(path: str) -> Path:
    target = Path(path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def remove_file_if_exists(path: str) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        return
