from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path

from municipal_research.config import load_config, load_municipalities
from municipal_research.pipeline import run_pipeline
from municipal_research.sharding import build_shard_plan, verify_run_hashes
from municipal_research.storage import canonical, digest, read_json, utc_now, write_json


def repository_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Input must be an existing file inside the repository: {value}")
    return path


def checkpoint_template(fingerprint: str, shard: dict, mode: str) -> dict:
    return {
        "schema_version": 1,
        "fingerprint": fingerprint,
        "shard_id": shard["id"],
        "shard_index": shard["index"],
        "mode": mode,
        "members": shard["municipality_ids"],
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "municipalities": {
            municipality_id: {"status": "pending", "attempts": 0}
            for municipality_id in shard["municipality_ids"]
        },
    }


def save_checkpoint(path: Path, checkpoint: dict) -> None:
    checkpoint["updated_at"] = utc_now()
    write_json(path, checkpoint)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one resumable municipality matrix shard")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--municipalities", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--state-root", type=Path, default=Path("research/shard-output"))
    parser.add_argument("--cache-root", type=Path, default=Path("data/cache/shards"))
    parser.add_argument("--expected", type=int, default=290)
    parser.add_argument("--target-size", type=int, default=25)
    parser.add_argument("--min-size", type=int, default=20)
    parser.add_argument("--max-size", type=int, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--mode", choices=["collect", "classify"])
    args = parser.parse_args()
    if args.retries < 0 or args.retries > 10:
        parser.error("--retries must be between 0 and 10")

    root = Path(__file__).resolve().parents[1]
    request_path = args.request.resolve()
    registry_path = args.municipalities.resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    config_path = repository_path(root, request["config"])
    mode = args.mode or os.getenv("RESEARCH_MODE") or request.get("mode", "collect")
    if mode == "classify" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for classify mode")

    municipalities = load_municipalities(registry_path)
    plan = build_shard_plan(
        municipalities,
        expected=args.expected,
        target_size=args.target_size,
        min_size=args.min_size,
        max_size=args.max_size,
    )
    if args.shard_index < 0 or args.shard_index >= len(plan["shards"]):
        raise ValueError(f"Shard index {args.shard_index} is outside the plan")
    shard = plan["shards"][args.shard_index]
    by_id = {municipality.id: municipality for municipality in municipalities}
    members = [by_id[municipality_id] for municipality_id in shard["municipality_ids"]]

    config = load_config(config_path)
    if "max_documents" in request:
        config.discovery.max_documents = int(request["max_documents"])
    if "max_chunks_per_document" in request:
        config.extraction.max_chunks_per_document = int(request["max_chunks_per_document"])
    call_limit = request.get("max_api_calls_per_municipality", request.get("max_api_calls"))
    if call_limit is not None:
        config.llm.max_calls = int(call_limit)
    config.network.user_agent = (
        "MunicipalResearch/0.1 (+https://github.com/Mihagley/Scraping_Swedish_elderly_care)"
    )

    fingerprint_data = {
        "registry_sha256": plan["registry_sha256"],
        "request_sha256": digest(request_path.read_bytes()),
        "config_sha256": digest(config_path.read_bytes()),
        "mode": mode,
        "shard": shard,
    }
    fingerprint = digest(canonical(fingerprint_data))
    shard_dir = args.state_root.resolve() / shard["id"]
    cache_dir = args.cache_root.resolve() / shard["id"]
    checkpoint_path = shard_dir / "checkpoint.json"
    shard_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if checkpoint_path.is_file():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("fingerprint") != fingerprint:
            raise ValueError(
                "Restored checkpoint belongs to different registry/request/config inputs; refusing reuse"
            )
        if checkpoint.get("members") != shard["municipality_ids"]:
            raise ValueError("Restored checkpoint membership does not match the current shard plan")
    else:
        checkpoint = checkpoint_template(fingerprint, shard, mode)
        save_checkpoint(checkpoint_path, checkpoint)

    failures: list[str] = []
    for municipality in members:
        state = checkpoint["municipalities"][municipality.id]
        if state.get("status") == "completed" and state.get("run"):
            try:
                verified = verify_run_hashes(shard_dir / state["run"])
                state["verified_artifacts"] = verified
                state["resume_action"] = "skipped_verified_complete"
                save_checkpoint(checkpoint_path, checkpoint)
                continue
            except Exception as error:
                state["status"] = "pending"
                state["resume_action"] = "rerun_after_hash_failure"
                state["last_error"] = f"{type(error).__name__}: {error}"
                save_checkpoint(checkpoint_path, checkpoint)

        completed = False
        for _ in range(args.retries + 1):
            state["attempts"] = int(state.get("attempts", 0)) + 1
            attempt = state["attempts"]
            relative_run = Path("runs") / municipality.id / f"attempt-{attempt:03d}"
            run_dir = shard_dir / relative_run
            state.update(
                status="running",
                run=relative_run.as_posix(),
                started_at=utc_now(),
                last_error=None,
            )
            save_checkpoint(checkpoint_path, checkpoint)
            try:
                result = run_pipeline(
                    config,
                    [municipality],
                    run_dir,
                    cache_dir,
                    collect_only=mode == "collect",
                    mode=f"{mode}:{shard['id']}",
                )
                verified = verify_run_hashes(run_dir)
                state.update(
                    status="completed",
                    completed_at=utc_now(),
                    verified_artifacts=verified,
                    summary=result["summary"][0],
                    errors=len(result["errors"]),
                )
                save_checkpoint(checkpoint_path, checkpoint)
                completed = True
                break
            except Exception as error:
                state.update(
                    status="failed",
                    failed_at=utc_now(),
                    last_error=f"{type(error).__name__}: {error}",
                    traceback="".join(traceback.format_exception(error))[-12000:],
                )
                save_checkpoint(checkpoint_path, checkpoint)
        if not completed:
            failures.append(municipality.id)

    statuses = checkpoint["municipalities"]
    complete_ids = [municipality_id for municipality_id, item in statuses.items() if item["status"] == "completed"]
    failed_ids = [municipality_id for municipality_id, item in statuses.items() if item["status"] != "completed"]
    manifest = {
        "schema_version": 1,
        "status": "completed" if not failed_ids else "incomplete",
        "mode": mode,
        "fingerprint": fingerprint,
        **fingerprint_data,
        "shard_id": shard["id"],
        "shard_index": shard["index"],
        "members": shard["municipality_ids"],
        "completed": complete_ids,
        "failed": failed_ids,
        "checkpoint_sha256": digest(checkpoint_path.read_bytes()),
        "finished_at": utc_now(),
    }
    write_json(shard_dir / "shard-manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if failures or failed_ids else 0


if __name__ == "__main__":
    raise SystemExit(main())
