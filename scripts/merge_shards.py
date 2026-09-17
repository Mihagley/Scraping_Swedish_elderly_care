from __future__ import annotations

import argparse
import json
from pathlib import Path

from municipal_research.config import load_municipalities
from municipal_research.export import export_workbook
from municipal_research.sharding import build_shard_plan, verify_run_hashes
from municipal_research.storage import canonical, digest, read_json, utc_now, write_json


def prefixed_path(shard_id: str, run_path: str, value: str) -> str:
    if not value:
        return value
    return f"artifact:research-{shard_id}/{shard_id}/{run_path}/{value}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge verified municipality shard outputs")
    parser.add_argument("--shards-root", type=Path, required=True)
    parser.add_argument("--municipalities", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected", type=int, default=290)
    parser.add_argument("--target-size", type=int, default=25)
    parser.add_argument("--min-size", type=int, default=20)
    parser.add_argument("--max-size", type=int, default=30)
    args = parser.parse_args()

    municipalities = load_municipalities(args.municipalities)
    plan = build_shard_plan(
        municipalities,
        expected=args.expected,
        target_size=args.target_size,
        min_size=args.min_size,
        max_size=args.max_size,
    )
    expected_ids = {municipality.id for municipality in municipalities}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        raise ValueError("Merge output directory must be empty")

    data = {
        "config": None,
        "summary": [],
        "documents": [],
        "classifications": [],
        "discovery": [],
        "errors": [],
        "shards": [],
        "merged_from_shards": True,
    }
    all_audit: list[dict] = []
    shard_manifests: list[dict] = []
    completed_ids: set[str] = set()
    gaps: list[str] = []
    config_signature: str | None = None
    mode: str | None = None

    for shard in plan["shards"]:
        shard_dir = args.shards_root / shard["id"]
        manifest_path = shard_dir / "shard-manifest.json"
        if not manifest_path.is_file():
            gaps.append(f"missing_shard:{shard['id']}")
            data["shards"].append(
                {
                    "shard_id": shard["id"],
                    "size": shard["size"],
                    "status": "missing",
                    "completed": 0,
                    "failed": shard["size"],
                    "manifest_sha256": None,
                    "checkpoint_sha256": None,
                }
            )
            continue
        shard_manifest = read_json(manifest_path)
        shard_manifests.append(shard_manifest)
        if shard_manifest.get("registry_sha256") != plan["registry_sha256"]:
            raise ValueError(f"Registry fingerprint mismatch in {shard['id']}")
        if shard_manifest.get("members") != shard["municipality_ids"]:
            raise ValueError(f"Membership mismatch in {shard['id']}")
        if mode is None:
            mode = shard_manifest["mode"]
        elif mode != shard_manifest["mode"]:
            raise ValueError("Cannot merge shards from different research modes")

        checkpoint_path = shard_dir / "checkpoint.json"
        if not checkpoint_path.is_file():
            gaps.append(f"missing_checkpoint:{shard['id']}")
            continue
        checkpoint = read_json(checkpoint_path)
        manifest_checkpoint_sha = shard_manifest.get("checkpoint_sha256")
        if manifest_checkpoint_sha != digest(checkpoint_path.read_bytes()):
            raise ValueError(f"Checkpoint hash mismatch in {shard['id']}")

        failed = 0
        for municipality_id in shard["municipality_ids"]:
            state = checkpoint["municipalities"].get(municipality_id, {})
            if state.get("status") != "completed" or not state.get("run"):
                failed += 1
                gaps.append(f"incomplete_municipality:{municipality_id}")
                continue
            if municipality_id in completed_ids:
                raise ValueError(f"Municipality appears in more than one shard: {municipality_id}")
            run_rel = state["run"]
            run_dir = shard_dir / run_rel
            verify_run_hashes(run_dir)
            result = read_json(run_dir / "results.json")
            run_manifest = read_json(run_dir / "manifest.json")
            signature = digest(canonical(result["config"]))
            if config_signature is None:
                config_signature = signature
                data["config"] = result["config"]
            elif signature != config_signature:
                raise ValueError("Cannot merge municipality runs with different resolved configs")
            if len(result["summary"]) != 1 or result["summary"][0]["municipality_id"] != municipality_id:
                raise ValueError(f"Unexpected summary membership in {run_dir}")

            completed_ids.add(municipality_id)
            data["summary"].extend(result["summary"])
            for document in result["documents"]:
                copied = dict(document)
                for field in ("raw_path", "text_path", "numbered_path"):
                    copied[field] = prefixed_path(shard["id"], run_rel, copied.get(field, ""))
                data["documents"].append(copied)
            data["classifications"].extend(result["classifications"])
            data["discovery"].extend(result["discovery"])
            data["errors"].extend(result["errors"])
            for line in (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                event["shard_id"] = shard["id"]
                all_audit.append(event)
            run_manifest_record = {
                "shard_id": shard["id"],
                "municipality_id": municipality_id,
                "run": run_rel,
                "status": run_manifest.get("status"),
                "manifest_sha256": digest((run_dir / "manifest.json").read_bytes()),
                "artifacts": len(run_manifest.get("artifacts", {})),
            }
            shard_manifest.setdefault("municipality_runs", []).append(run_manifest_record)

        data["shards"].append(
            {
                "shard_id": shard["id"],
                "size": shard["size"],
                "status": shard_manifest.get("status"),
                "completed": shard["size"] - failed,
                "failed": failed,
                "manifest_sha256": digest(manifest_path.read_bytes()),
                "checkpoint_sha256": manifest_checkpoint_sha,
            }
        )

    missing_ids = sorted(expected_ids - completed_ids)
    extra_ids = sorted(completed_ids - expected_ids)
    if extra_ids:
        raise ValueError("Merged unexpected municipalities: " + ", ".join(extra_ids))
    if missing_ids:
        gaps.extend(f"missing_municipality:{municipality_id}" for municipality_id in missing_ids)
    if data["config"] is None:
        raise RuntimeError("No completed municipality runs were available to merge")

    data["summary"].sort(key=lambda row: row["municipality_id"])
    write_json(args.output_dir / "results.json", data)
    write_json(args.output_dir / "shard-manifests.json", shard_manifests)
    with (args.output_dir / "audit.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for event in all_audit:
            handle.write(canonical(event) + "\n")

    complete = not gaps and completed_ids == expected_ids
    manifest = {
        "version": 1,
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "status": "completed" if complete else "incomplete",
        "mode": mode or "unknown",
        "registry_sha256": plan["registry_sha256"],
        "expected_municipalities": len(expected_ids),
        "completed_municipalities": len(completed_ids),
        "shards": len(plan["shards"]),
        "gaps": sorted(set(gaps)),
    }
    write_json(args.output_dir / "manifest.json", manifest)
    export_workbook(args.output_dir)

    report = [
        "# Shard merge status",
        "",
        f"Status: **{manifest['status']}**",
        f"Municipalities merged: **{len(completed_ids)} / {len(expected_ids)}**",
        f"Shards expected: **{len(plan['shards'])}**",
        "",
        "Coverage remains bounded by the discovery strategy and recorded gaps; a completed merge is not a claim of exhaustive web coverage.",
    ]
    if gaps:
        report += ["", "Merge gaps:", *[f"- {gap}" for gap in sorted(set(gaps))]]
    (args.output_dir / "MERGE_STATUS.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    manifest["artifacts"] = {
        path.relative_to(args.output_dir).as_posix(): digest(path.read_bytes())
        for path in sorted(args.output_dir.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    write_json(args.output_dir / "manifest.json", manifest)
    verify_run_hashes(args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
