from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path

from municipal_research.classification import Classifier
from municipal_research.config import load_config, load_municipalities
from municipal_research.export import export_workbook
from municipal_research.llm import Gateway
from municipal_research.models import Chunk, Document
from municipal_research.pipeline import summarize
from municipal_research.sharding import build_shard_plan, verify_run_hashes
from municipal_research.storage import Audit, canonical, digest, read_json, utc_now, write_json
from municipal_research.triage import select_chunks


def repository_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Input must be an existing file inside the repository: {value}")
    return path


def save_checkpoint(path: Path, checkpoint: dict) -> None:
    checkpoint["updated_at"] = utc_now()
    write_json(path, checkpoint)


def artifact_source_path(shard_id: str, source_run: str, value: str) -> str:
    if not value:
        return value
    return f"artifact:research-{shard_id}/{shard_id}/{source_run}/{value}"


def classify_one(
    *,
    config,
    municipality,
    source_run: Path,
    source_run_rel: str,
    shard_id: str,
    output_run: Path,
    cache_dir: Path,
) -> dict:
    verify_run_hashes(source_run)
    if output_run.exists() and any(output_run.iterdir()):
        raise ValueError("Classification run directory must be empty")
    output_run.mkdir(parents=True, exist_ok=True)
    audit = Audit(output_run / "audit.jsonl")
    source = read_json(source_run / "results.json")
    if len(source["summary"]) != 1 or source["summary"][0]["municipality_id"] != municipality.id:
        raise ValueError("Source collection run has unexpected municipality membership")

    source_documents = source["documents"]
    document_meta = {document["id"]: document for document in source_documents}
    chunks = [
        Chunk.model_validate(read_json(path))
        for path in sorted((source_run / "chunks").glob("*.json"))
    ]
    unknown_document_ids = sorted({chunk.document_id for chunk in chunks} - set(document_meta))
    if unknown_document_ids:
        raise ValueError(
            "Collected chunks refer to unknown documents: " + ", ".join(unknown_document_ids)
        )

    # Reserve for the worst case per chunk: independent passes + adjudication + verification.
    max_selected = max(1, config.llm.max_calls // (config.llm.passes + 2))
    selected, triage_rows, triage_limited = select_chunks(
        chunks, document_meta, max_selected=max_selected, context_neighbors=1
    )
    write_json(
        output_run / "triage.json",
        {
            "method": "deterministic_local_v1",
            "total_collected_chunks": len(chunks),
            "selected_chunks": len(selected),
            "max_selected": max_selected,
            "limited": triage_limited,
            "chunks": triage_rows,
        },
    )
    audit.emit(
        "triage",
        municipality_id=municipality.id,
        total_chunks=len(chunks),
        selected_chunks=len(selected),
        max_selected=max_selected,
        limited=triage_limited,
    )

    output_documents = []
    documents: dict[str, Document] = {}
    for metadata in source_documents:
        text_path = source_run / metadata["text_path"]
        text = text_path.read_text(encoding="utf-8")
        if digest(text) != metadata["text_sha256"]:
            raise ValueError(f"Collected text hash mismatch for {metadata['id']}")
        documents[metadata["id"]] = Document.model_validate({**metadata, "text": text})
        copied = dict(metadata)
        for field in ("raw_path", "text_path", "numbered_path"):
            copied[field] = artifact_source_path(shard_id, source_run_rel, copied.get(field, ""))
        output_documents.append(copied)

    gateway = Gateway(config.llm, cache_dir / "llm", output_run, audit)
    classifier = Classifier(config, gateway, audit)
    records: list[dict] = []
    classification_errors: list[dict] = []
    try:
        for chunk in selected:
            record = classifier.classify(municipality, documents[chunk.document_id], chunk)
            records.append(record)
            write_json(output_run / "units" / (digest(chunk.id) + ".json"), record)
            for error in record["errors"]:
                classification_errors.append(
                    {
                        "municipality_id": municipality.id,
                        "stage": "classification",
                        "url": record["url"],
                        "detail": error,
                    }
                )
    finally:
        gateway.close()

    gaps = list(source["summary"][0].get("gaps", []))
    if triage_limited:
        gaps.append("local_triage_budget_limited")
    if not selected:
        gaps.append("no_locally_relevant_chunks")
    summary = summarize(municipality, output_documents, records, gaps, config, collect_only=False)
    summary["collected_chunks"] = len(chunks)
    summary["triaged_chunks"] = len(selected)

    data = {
        "config": config.model_dump(mode="json"),
        "summary": [summary],
        "documents": output_documents,
        "classifications": records,
        "discovery": source["discovery"],
        "errors": [*source["errors"], *classification_errors],
        "triage": triage_rows,
        "source_collection": {
            "shard_id": shard_id,
            "run": source_run_rel,
            "manifest_sha256": digest((source_run / "manifest.json").read_bytes()),
        },
    }
    write_json(output_run / "results.json", data)
    manifest = {
        "version": 1,
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "status": "completed_with_gaps"
        if summary["needs_review"] or data["errors"]
        else "completed",
        "mode": "classify-collected",
        "municipality_id": municipality.id,
        "source_collection_manifest_sha256": data["source_collection"]["manifest_sha256"],
        "api_calls": gateway.calls,
        "triage_total_chunks": len(chunks),
        "triage_selected_chunks": len(selected),
        "triage_limited": triage_limited,
    }
    write_json(output_run / "manifest.json", manifest)
    export_workbook(output_run)
    manifest["artifacts"] = {
        path.relative_to(output_run).as_posix(): digest(path.read_bytes())
        for path in sorted(output_run.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    write_json(output_run / "manifest.json", manifest)
    verify_run_hashes(output_run)
    return {"summary": summary, "errors": data["errors"], "api_calls": gateway.calls}


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a previously collected immutable shard")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--municipalities", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--state-root", type=Path, default=Path("research/classification-output"))
    parser.add_argument("--cache-root", type=Path, default=Path("data/cache/classification"))
    parser.add_argument("--expected", type=int, default=290)
    parser.add_argument("--target-size", type=int, default=25)
    parser.add_argument("--min-size", type=int, default=20)
    parser.add_argument("--max-size", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for collected-data classification")

    root = Path(__file__).resolve().parents[1]
    request_path = args.request.resolve()
    registry_path = args.municipalities.resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    config_path = repository_path(root, request["config"])
    config = load_config(config_path)
    call_limit = request.get("max_api_calls_per_municipality", request.get("max_api_calls"))
    if call_limit is not None:
        config.llm.max_calls = int(call_limit)

    municipalities = load_municipalities(registry_path)
    plan = build_shard_plan(
        municipalities,
        expected=args.expected,
        target_size=args.target_size,
        min_size=args.min_size,
        max_size=args.max_size,
    )
    shard = plan["shards"][args.shard_index]
    by_id = {municipality.id: municipality for municipality in municipalities}
    source_shard = args.source_root.resolve() / shard["id"]
    source_checkpoint = read_json(source_shard / "checkpoint.json")
    source_manifest = read_json(source_shard / "shard-manifest.json")
    if source_manifest.get("members") != shard["municipality_ids"]:
        raise ValueError("Source collection shard membership does not match current plan")
    if source_manifest.get("registry_sha256") != plan["registry_sha256"]:
        raise ValueError("Source collection registry fingerprint does not match current registry")

    fingerprint_data = {
        "registry_sha256": plan["registry_sha256"],
        "request_sha256": digest(request_path.read_bytes()),
        "config_sha256": digest(config_path.read_bytes()),
        "mode": "classify-collected",
        "shard": shard,
        "source_shard_manifest_sha256": digest((source_shard / "shard-manifest.json").read_bytes()),
        "source_checkpoint_sha256": digest((source_shard / "checkpoint.json").read_bytes()),
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
            raise ValueError("Restored classification checkpoint belongs to different inputs")
    else:
        checkpoint = {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "shard_id": shard["id"],
            "shard_index": shard["index"],
            "mode": "classify-collected",
            "members": shard["municipality_ids"],
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "municipalities": {
                municipality_id: {"status": "pending", "attempts": 0}
                for municipality_id in shard["municipality_ids"]
            },
        }
        save_checkpoint(checkpoint_path, checkpoint)

    failures: list[str] = []
    source_incomplete: list[str] = []
    for municipality_id in shard["municipality_ids"]:
        municipality = by_id[municipality_id]
        state = checkpoint["municipalities"][municipality_id]
        source_state = source_checkpoint["municipalities"].get(municipality_id, {})
        if source_state.get("status") != "completed" or not source_state.get("run"):
            state.update(status="source_incomplete", source_status=source_state.get("status"))
            source_incomplete.append(municipality_id)
            save_checkpoint(checkpoint_path, checkpoint)
            continue
        if state.get("status") == "completed" and state.get("run"):
            try:
                verify_run_hashes(shard_dir / state["run"])
                state["resume_action"] = "skipped_verified_complete"
                save_checkpoint(checkpoint_path, checkpoint)
                continue
            except Exception as error:
                state.update(status="pending", last_error=f"{type(error).__name__}: {error}")
                save_checkpoint(checkpoint_path, checkpoint)

        completed = False
        for _ in range(args.retries + 1):
            state["attempts"] = int(state.get("attempts", 0)) + 1
            attempt = state["attempts"]
            relative_run = Path("runs") / municipality_id / f"attempt-{attempt:03d}"
            output_run = shard_dir / relative_run
            state.update(
                status="running", run=relative_run.as_posix(), started_at=utc_now(), last_error=None
            )
            save_checkpoint(checkpoint_path, checkpoint)
            try:
                result = classify_one(
                    config=config,
                    municipality=municipality,
                    source_run=source_shard / source_state["run"],
                    source_run_rel=source_state["run"],
                    shard_id=shard["id"],
                    output_run=output_run,
                    cache_dir=cache_dir / municipality_id,
                )
                state.update(
                    status="completed",
                    completed_at=utc_now(),
                    summary=result["summary"],
                    errors=len(result["errors"]),
                    api_calls=result["api_calls"],
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
            failures.append(municipality_id)

    completed_ids = [
        municipality_id
        for municipality_id, item in checkpoint["municipalities"].items()
        if item["status"] == "completed"
    ]
    manifest = {
        "schema_version": 1,
        "status": "completed" if not failures else "incomplete",
        **fingerprint_data,
        "fingerprint": fingerprint,
        "shard_id": shard["id"],
        "shard_index": shard["index"],
        "members": shard["municipality_ids"],
        "completed": completed_ids,
        "failed": failures,
        "source_incomplete": source_incomplete,
        "checkpoint_sha256": digest(checkpoint_path.read_bytes()),
        "finished_at": utc_now(),
    }
    write_json(shard_dir / "shard-manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
