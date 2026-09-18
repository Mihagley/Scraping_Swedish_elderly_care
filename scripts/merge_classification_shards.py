from __future__ import annotations

import argparse
import json
from pathlib import Path

from municipal_research.config import load_municipalities
from municipal_research.export import export_workbook
from municipal_research.sharding import build_shard_plan, verify_run_hashes
from municipal_research.storage import canonical, digest, read_json, utc_now, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge verified classify-collected shard outputs")
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
        "triage": [],
        "shards": [],
        "merged_from_classification_shards": True,
    }
    all_audit: list[dict] = []
    completed_ids: set[str] = set()
    source_incomplete_ids: set[str] = set()
    failures: list[str] = []
    config_signature: str | None = None

    for shard in plan["shards"]:
        shard_dir = args.shards_root / shard["id"]
        manifest_path = shard_dir / "shard-manifest.json"
        checkpoint_path = shard_dir / "checkpoint.json"
        if not manifest_path.is_file() or not checkpoint_path.is_file():
            failures.append(f"missing_shard:{shard['id']}")
            continue
        shard_manifest = read_json(manifest_path)
        checkpoint = read_json(checkpoint_path)
        if shard_manifest.get("registry_sha256") != plan["registry_sha256"]:
            raise ValueError(f"Registry fingerprint mismatch in {shard['id']}")
        if shard_manifest.get("members") != shard["municipality_ids"]:
            raise ValueError(f"Membership mismatch in {shard['id']}")
        if shard_manifest.get("mode") != "classify-collected":
            raise ValueError(f"Unexpected mode in {shard['id']}")
        if shard_manifest.get("checkpoint_sha256") != digest(checkpoint_path.read_bytes()):
            raise ValueError(f"Checkpoint hash mismatch in {shard['id']}")
        source_incomplete_ids.update(shard_manifest.get("source_incomplete", []))

        completed = 0
        failed = 0
        for municipality_id in shard["municipality_ids"]:
            state = checkpoint["municipalities"].get(municipality_id, {})
            if state.get("status") == "source_incomplete":
                source_incomplete_ids.add(municipality_id)
                continue
            if state.get("status") != "completed" or not state.get("run"):
                failed += 1
                failures.append(f"classification_incomplete:{municipality_id}")
                continue
            if municipality_id in completed_ids:
                raise ValueError(f"Municipality appears in more than one shard: {municipality_id}")
            run_dir = shard_dir / state["run"]
            verify_run_hashes(run_dir)
            result = read_json(run_dir / "results.json")
            signature = digest(canonical(result["config"]))
            if config_signature is None:
                config_signature = signature
                data["config"] = result["config"]
            elif signature != config_signature:
                raise ValueError("Classification runs used different resolved configs")
            if (
                len(result["summary"]) != 1
                or result["summary"][0]["municipality_id"] != municipality_id
            ):
                raise ValueError(f"Unexpected summary membership in {run_dir}")
            completed_ids.add(municipality_id)
            completed += 1
            data["summary"].extend(result["summary"])
            data["documents"].extend(result["documents"])
            data["classifications"].extend(result["classifications"])
            data["discovery"].extend(result["discovery"])
            data["errors"].extend(result["errors"])
            data["triage"].extend(result.get("triage", []))
            for line in (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                event["shard_id"] = shard["id"]
                all_audit.append(event)
        data["shards"].append(
            {
                "shard_id": shard["id"],
                "size": shard["size"],
                "completed": completed,
                "source_incomplete": sum(
                    municipality_id in source_incomplete_ids
                    for municipality_id in shard["municipality_ids"]
                ),
                "failed": failed,
                "manifest_sha256": digest(manifest_path.read_bytes()),
            }
        )

    if data["config"] is None:
        raise RuntimeError("No completed classification runs were available to merge")
    missing_ids = expected_ids - completed_ids
    unexpected_missing = sorted(missing_ids - source_incomplete_ids)
    if unexpected_missing:
        failures.extend(
            f"missing_classification:{municipality_id}" for municipality_id in unexpected_missing
        )
    extra = sorted(completed_ids - expected_ids)
    if extra:
        raise ValueError("Merged unexpected municipalities: " + ", ".join(extra))

    data["summary"].sort(key=lambda row: row["municipality_id"])
    write_json(args.output_dir / "results.json", data)
    with (args.output_dir / "audit.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for event in all_audit:
            handle.write(canonical(event) + "\n")

    status = (
        "completed_with_source_gaps"
        if source_incomplete_ids and not failures
        else "completed"
        if not failures
        else "incomplete"
    )
    manifest = {
        "version": 1,
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "status": status,
        "mode": "classify-collected",
        "registry_sha256": plan["registry_sha256"],
        "expected_municipalities": len(expected_ids),
        "classified_municipalities": len(completed_ids),
        "source_incomplete_municipalities": sorted(source_incomplete_ids),
        "classification_failures": sorted(set(failures)),
        "shards": len(plan["shards"]),
    }
    write_json(args.output_dir / "manifest.json", manifest)
    export_workbook(args.output_dir)
    report = [
        "# Classification merge status",
        "",
        f"Status: **{status}**",
        f"Municipalities classified: **{len(completed_ids)} / {len(expected_ids)}**",
        f"Accepted source gaps: **{len(source_incomplete_ids)}**",
        f"Classification failures: **{len(set(failures))}**",
        "",
        "Source gaps remain explicit and are not interpreted as negative findings.",
    ]
    if source_incomplete_ids:
        report += [
            "",
            "Source-incomplete municipalities:",
            *[f"- {item}" for item in sorted(source_incomplete_ids)],
        ]
    if failures:
        report += ["", "Classification failures:", *[f"- {item}" for item in sorted(set(failures))]]
    (args.output_dir / "MERGE_STATUS.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    manifest["artifacts"] = {
        path.relative_to(args.output_dir).as_posix(): digest(path.read_bytes())
        for path in sorted(args.output_dir.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    write_json(args.output_dir / "manifest.json", manifest)
    verify_run_hashes(args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
