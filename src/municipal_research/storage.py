from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(
        path, (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Audit:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, stage: str, **data) -> None:
        event = {"timestamp": utc_now(), "stage": stage, **data}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(event) + "\n")
        logging.getLogger("municipal_research").info("%s %s", stage, canonical(data))


def environment() -> dict:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            d.metadata["Name"]: d.version for d in distributions() if d.metadata.get("Name")
        },
    }
