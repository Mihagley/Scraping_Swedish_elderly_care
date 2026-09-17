from __future__ import annotations

import re
from pathlib import Path

from .config import Municipality
from .storage import canonical, digest, read_json


def validate_registry(municipalities: list[Municipality], expected: int = 290) -> None:
    if len(municipalities) != expected:
        raise ValueError(f"Expected {expected} municipalities, found {len(municipalities)}")
    ids = [municipality.id for municipality in municipalities]
    names = [municipality.name.casefold().strip() for municipality in municipalities]
    if len(set(ids)) != len(ids):
        raise ValueError("Municipality ids must be unique")
    if len(set(names)) != len(names):
        raise ValueError("Municipality names must be unique")
    bad_ids = [
        municipality.id
        for municipality in municipalities
        if not re.fullmatch(r"\d{4}", municipality.id)
    ]
    if bad_ids:
        raise ValueError("Swedish municipality ids must be four digits: " + ", ".join(bad_ids))


def registry_sha256(municipalities: list[Municipality]) -> str:
    return digest(
        canonical([municipality.model_dump(mode="json") for municipality in municipalities])
    )


def balanced_shards(
    municipalities: list[Municipality],
    *,
    target_size: int = 25,
    min_size: int = 20,
    max_size: int = 30,
) -> list[list[Municipality]]:
    if not municipalities:
        raise ValueError("Cannot shard an empty municipality registry")
    if not 1 <= min_size <= target_size <= max_size:
        raise ValueError("Require 1 <= min_size <= target_size <= max_size")
    count = max(1, round(len(municipalities) / target_size))
    while count > 1 and len(municipalities) // count < min_size:
        count -= 1
    while (len(municipalities) + count - 1) // count > max_size:
        count += 1
    base, remainder = divmod(len(municipalities), count)
    sizes = [base + (1 if index < remainder else 0) for index in range(count)]
    if min(sizes) < min_size or max(sizes) > max_size:
        raise ValueError(
            f"Cannot split {len(municipalities)} municipalities into {min_size}-{max_size} sized shards"
        )
    shards: list[list[Municipality]] = []
    start = 0
    for size in sizes:
        shards.append(municipalities[start : start + size])
        start += size
    return shards


def build_shard_plan(
    municipalities: list[Municipality],
    *,
    expected: int = 290,
    target_size: int = 25,
    min_size: int = 20,
    max_size: int = 30,
) -> dict:
    validate_registry(municipalities, expected)
    shards = balanced_shards(
        municipalities,
        target_size=target_size,
        min_size=min_size,
        max_size=max_size,
    )
    return {
        "schema_version": 1,
        "registry_sha256": registry_sha256(municipalities),
        "municipalities": len(municipalities),
        "target_size": target_size,
        "min_size": min_size,
        "max_size": max_size,
        "shards": [
            {
                "index": index,
                "id": f"shard-{index:02d}",
                "size": len(shard),
                "municipality_ids": [municipality.id for municipality in shard],
            }
            for index, shard in enumerate(shards)
        ],
    }


def verify_run_hashes(run_dir: Path) -> int:
    manifest = read_json(run_dir / "manifest.json")
    artifacts = manifest.get("artifacts", {})
    if not artifacts:
        raise ValueError(f"Run has no completed artifact manifest: {run_dir}")
    bad = [
        name
        for name, expected in artifacts.items()
        if not (run_dir / name).is_file() or digest((run_dir / name).read_bytes()) != expected
    ]
    if bad:
        raise ValueError("Missing or modified artifacts: " + ", ".join(bad))
    return len(artifacts)
