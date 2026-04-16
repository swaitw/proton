from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional


def _base_dir() -> Path:
    root = Path(os.getenv("PROTON_DATA_DIR") or "./data")
    path = root / "integrations_media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_bytes(*, portal_id: str, channel: str, data: bytes, suffix: str, hint: str = "") -> str:
    base = _base_dir() / portal_id / channel
    base.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(data).hexdigest()[:12]
    ts = int(time.time())
    name = f"{ts}-{digest}"
    if hint:
        safe = "".join(ch for ch in hint if ch.isalnum() or ch in ("-", "_"))[:32]
        if safe:
            name = f"{name}-{safe}"
    path = base / f"{name}{suffix}"
    path.write_bytes(data)
    return str(path)

