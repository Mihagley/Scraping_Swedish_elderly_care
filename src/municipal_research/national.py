from __future__ import annotations

import hashlib
import json
from pathlib import Path


def shard_bounds(total: int, shard_count: int, shard_index: int) -> tuple[int, int]:
    if total < 1 or shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("Invalid shard parameters")
    base, remainder = divmod(total, shard_count)
    start = shard_index * base + min(shard_index, remainder)
    size = base + (1 if shard_index < remainder else 0)
    return start, start + size


def shard_items(items: list, shard_count: int, shard_index: int) -> list:
    start, end = shard_bounds(len(items), shard_count, shard_index)
    selected = items[start:end]
    if len(items) == 290 and not 20 <= len(selected) <= 30:
        raise ValueError(f"National shard has invalid size {len(selected)}")
    return selected


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_manifest(run: Path) -> list[str]:
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    issues = []
    for relative, expected in manifest.get("artifacts", {}).items():
        path = run / relative
        if not path.is_file():
            issues.append(f"missing:{relative}")
        elif sha256_file(path) != expected:
            issues.append(f"hash:{relative}")
    return issues
