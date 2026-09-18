"""Run one 20-30 municipality national shard with per-municipality checkpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

from municipal_research.config import load_config, load_municipalities
from municipal_research.national import shard_items, verify_manifest
from municipal_research.pipeline import run_pipeline
from municipal_research.storage import write_json


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    shard_index = int(os.environ["SHARD_INDEX"])
    shard_count = int(os.getenv("SHARD_COUNT", "12"))
    mode = os.getenv("RESEARCH_MODE", "classify")
    if mode not in {"collect", "classify"}:
        raise ValueError("RESEARCH_MODE must be collect or classify")
    if mode == "classify" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY repository secret is required for classify mode")

    municipalities = load_municipalities(root / "research/municipalities-290.csv")
    if len(municipalities) != 290:
        raise RuntimeError(f"National input has {len(municipalities)} rows, expected 290")
    selected = shard_items(municipalities, shard_count, shard_index)
    config = load_config(root / "research/national-language-requirements.yaml")
    run_id = os.getenv("NATIONAL_RUN_ID") or os.getenv("GITHUB_RUN_ID") or "local-national"
    shard = root / "research/national-runs" / run_id / f"shard-{shard_index:02d}"
    cache = root / "data/national-cache" / f"shard-{shard_index:02d}"
    shard.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "run_id": run_id,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "mode": mode,
        "municipality_ids": [m.id for m in selected],
        "completed": [],
        "failed": [],
    }
    checkpoint_path = shard / "checkpoint.json"
    if checkpoint_path.exists():
        prior = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if prior.get("municipality_ids") == checkpoint["municipality_ids"]:
            checkpoint = prior

    for municipality in selected:
        run = shard / "municipalities" / municipality.id
        if (run / "manifest.json").is_file():
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("status", "").startswith("completed") and not verify_manifest(run):
                if municipality.id not in checkpoint["completed"]:
                    checkpoint["completed"].append(municipality.id)
                write_json(checkpoint_path, checkpoint)
                continue
        # A failed partial directory is evidence/audit material, not a valid resume point.
        # Move it aside; HTTP and LLM responses resume from the shard cache.
        if run.exists():
            suffix = 1
            while (run.parent / f"{municipality.id}.failed-{suffix}").exists():
                suffix += 1
            run.rename(run.parent / f"{municipality.id}.failed-{suffix}")
        try:
            run_pipeline(
                config,
                [municipality],
                run,
                cache,
                collect_only=mode == "collect",
                mode=f"national-shard-{shard_index:02d}-{mode}",
            )
            issues = verify_manifest(run)
            if issues:
                raise RuntimeError("Artifact hash verification failed: " + ", ".join(issues[:10]))
            checkpoint["completed"] = sorted(set(checkpoint["completed"] + [municipality.id]))
            checkpoint["failed"] = [
                x for x in checkpoint["failed"] if x.get("municipality_id") != municipality.id
            ]
        except Exception as error:
            checkpoint["failed"] = [
                x for x in checkpoint["failed"] if x.get("municipality_id") != municipality.id
            ]
            checkpoint["failed"].append(
                {"municipality_id": municipality.id, "error": f"{type(error).__name__}: {error}"}
            )
            write_json(checkpoint_path, checkpoint)
            raise
        write_json(checkpoint_path, checkpoint)

    write_json(
        shard / "shard-manifest.json",
        {
            **checkpoint,
            "status": "completed"
            if len(checkpoint["completed"]) == len(selected) and not checkpoint["failed"]
            else "incomplete",
            "municipality_count": len(selected),
        },
    )
    if len(checkpoint["completed"]) != len(selected) or checkpoint["failed"]:
        raise RuntimeError("Shard did not complete every municipality")


if __name__ == "__main__":
    main()
