"""Merge verified national shard checkpoints into one auditable workbook/package."""
from __future__ import annotations

import json
import os
from pathlib import Path

from municipal_research.export import export_workbook
from municipal_research.national import sha256_file, verify_manifest
from municipal_research.storage import digest, utc_now, write_json

LIST_KEYS = ["summary", "documents", "classifications", "discovery", "triage", "pending_searches", "errors"]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    run_id = os.getenv("NATIONAL_RUN_ID") or os.getenv("GITHUB_RUN_ID") or "local-national"
    shard_count = int(os.getenv("SHARD_COUNT", "12"))
    base = root / "research/national-runs" / run_id
    final = base / "final"
    final.mkdir(parents=True, exist_ok=True)

    merged = {key: [] for key in LIST_KEYS}
    merged["config"] = None
    shard_manifests, all_audit = [], []
    seen_municipalities = set()

    for shard_index in range(shard_count):
        shard = base / f"shard-{shard_index:02d}"
        shard_manifest_path = shard / "shard-manifest.json"
        if not shard_manifest_path.is_file():
            raise RuntimeError(f"Missing shard manifest: {shard_manifest_path}")
        shard_manifest = json.loads(shard_manifest_path.read_text(encoding="utf-8"))
        if shard_manifest.get("status") != "completed":
            raise RuntimeError(f"Shard {shard_index} is not complete")
        shard_manifests.append({
            "shard_index": shard_index,
            "status": shard_manifest["status"],
            "municipality_count": shard_manifest["municipality_count"],
            "manifest_path": shard_manifest_path.relative_to(base).as_posix(),
            "manifest_sha256": sha256_file(shard_manifest_path),
        })
        for municipality_id in shard_manifest["municipality_ids"]:
            run = shard / "municipalities" / municipality_id
            issues = verify_manifest(run)
            if issues:
                raise RuntimeError(f"Hash failure in {municipality_id}: {issues[:5]}")
            data = json.loads((run / "results.json").read_text(encoding="utf-8"))
            if merged["config"] is None:
                merged["config"] = data["config"]
            elif data["config"] != merged["config"]:
                raise RuntimeError("Shard configuration mismatch")
            if municipality_id in seen_municipalities:
                raise RuntimeError(f"Duplicate municipality across shards: {municipality_id}")
            seen_municipalities.add(municipality_id)
            prefix = run.relative_to(base).as_posix()
            for document in data["documents"]:
                document = dict(document)
                for key in ["raw_path", "text_path", "numbered_path"]:
                    if document.get(key):
                        document[key] = f"{prefix}/{document[key]}"
                merged["documents"].append(document)
            for key in LIST_KEYS:
                if key != "documents":
                    merged[key].extend(data.get(key, []))
            audit_path = run / "audit.jsonl"
            if audit_path.exists():
                for line in audit_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    event["shard_index"] = shard_index
                    event["municipality_run"] = prefix
                    all_audit.append(event)

    if len(seen_municipalities) != 290:
        raise RuntimeError(f"Merged {len(seen_municipalities)} municipalities, expected 290")
    merged["summary"].sort(key=lambda row: row["municipality_id"])
    write_json(final / "results.json", merged)
    with (final / "audit.jsonl").open("w", encoding="utf-8") as handle:
        for event in all_audit:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {
        "mode": "national-merged",
        "started_at": utc_now(),
        "status": "completed_with_gaps" if any(r.get("needs_review") or r.get("gaps") for r in merged["summary"]) else "completed",
        "municipalities": 290,
        "coverage_claim": "bounded_gap_aware",
        "shard_manifests": shard_manifests,
        "results_sha256": sha256_file(final / "results.json"),
        "audit_sha256": sha256_file(final / "audit.jsonl"),
    }
    write_json(final / "manifest.json", manifest)
    export_workbook(final, final / "swedish-elderly-care-language-requirements-290.xlsx")
    manifest["artifacts"] = {
        p.relative_to(final).as_posix(): digest(p.read_bytes())
        for p in sorted(final.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    manifest["finished_at"] = utc_now()
    write_json(final / "manifest.json", manifest)
    for relative, expected in manifest["artifacts"].items():
        actual = sha256_file(final / relative)
        if actual != expected:
            raise RuntimeError(f"Final artifact hash changed: {relative}")
    print(json.dumps({"final": str(final), "municipalities": 290, "status": manifest["status"]}, indent=2))


if __name__ == "__main__":
    main()
