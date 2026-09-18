from __future__ import annotations

import logging
from pathlib import Path

from . import __version__
from .classification import Classifier
from .config import Config, Municipality
from .discovery import Discoverer
from .export import export_workbook
from .extraction import chunk_document, extract
from .llm import Gateway
from .network import Fetcher
from .storage import Audit, canonical, digest, environment, utc_now, write_json
from .triage import triage_chunk

DATE_FIELDS = ["publication_date", "decision_date", "implementation_date", "in_force_by_date", "effective_date"]


def summarize(
    municipality: Municipality,
    documents: list[dict],
    records: list[dict],
    gaps: list[str],
    config: Config,
    collect_only=False,
    *,
    chunks_scanned: int = 0,
    chunks_classified: int = 0,
) -> dict:
    accepted = [r for r in records if config.research.labels[r["decision"]["category"]].conclusive]
    labels = {r["decision"]["category"] for r in accepted}
    conflicts = []
    for key in [*DATE_FIELDS, "scope"]:
        if len({r["decision"].get(key) for r in accepted if r["decision"].get(key)}) > 1:
            conflicts.append(key)
    for key in config.research.fields:
        values = {a["value"] for r in accepted for a in r["decision"]["attributes"] if a["name"] == key}
        if len(values) > 1:
            conflicts.append(key)
    category = (
        "NotClassified"
        if collect_only
        else "Review"
        if len(labels) > 1 or conflicts
        else next(iter(labels))
        if labels
        else config.research.unknown_label
    )
    if conflicts:
        gaps = gaps + ["conflicting_" + k for k in conflicts]
    needs_review = collect_only or not labels or len(labels) > 1 or bool(gaps) or bool(conflicts) or any(r["needs_review"] for r in records)
    result = {
        "municipality_id": municipality.id,
        "municipality": municipality.name,
        "category": category,
        "needs_review": needs_review,
        "documents": len(documents),
        "chunks_scanned": chunks_scanned,
        "chunks_classified": chunks_classified,
        "chunks": len(records),
        "verified_quotes": sum(q["verified"] for r in records for q in r["quotes"]),
        "coverage": "bounded_with_gaps" if gaps else "bounded",
        "gaps": sorted(set(gaps)),
        "meeting_archives": municipality.meeting_archives,
        "source_urls": sorted({d["url"] for d in documents}),
        "scopes": sorted({r["decision"]["scope"] for r in accepted if r["decision"]["scope"]}),
    }
    for field in DATE_FIELDS:
        result[field + "s"] = sorted({r["decision"].get(field) for r in accepted if r["decision"].get(field)})
    result.update(
        {
            f"field:{key}": sorted({a["value"] for r in accepted for a in r["decision"]["attributes"] if a["name"] == key})
            for key in config.research.fields
        }
    )
    return result


def run_pipeline(
    config: Config,
    municipalities: list[Municipality],
    run: Path,
    cache: Path,
    *,
    offline=False,
    refresh=False,
    collect_only=False,
    mode="live",
    fetcher_factory=None,
    gateway_factory=None,
) -> dict:
    if run.exists() and any(run.iterdir()):
        raise ValueError("Run directory is not empty; choose a new directory (cache is reusable)")
    if offline and refresh:
        raise ValueError("--offline and --refresh are mutually exclusive")
    if cache.resolve().is_relative_to(run.resolve()):
        raise ValueError("Keep the shared cache outside the immutable run directory")
    run.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(run / "pipeline.log", encoding="utf-8")
    logger = logging.getLogger("municipal_research")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    audit = Audit(run / "audit.jsonl")
    config_data = config.model_dump(mode="json")
    inputs = [m.model_dump() for m in municipalities]
    write_json(run / "config.resolved.json", config_data)
    write_json(run / "municipalities.json", inputs)
    source_hashes = {p.name: digest(p.read_bytes()) for p in Path(__file__).parent.glob("*.py")}
    manifest = {
        "version": __version__,
        "started_at": utc_now(),
        "status": "running",
        "mode": mode,
        "offline": offline,
        "collect_only": collect_only,
        "config_sha256": digest(canonical(config_data)),
        "inputs_sha256": digest(canonical(inputs)),
        "source_code_sha256": source_hashes,
        "environment": environment(),
        "cache_dir": str(cache.resolve()),
    }
    write_json(run / "manifest.json", manifest)
    fetcher = (fetcher_factory or Fetcher)(config.network, cache / "http", audit, offline=offline, refresh=refresh)
    gateway = (gateway_factory or Gateway)(config.llm, cache / "llm", run, audit, offline=offline, refresh=refresh)
    classifier = Classifier(config, gateway, audit)
    data = {
        "config": config_data,
        "summary": [],
        "documents": [],
        "classifications": [],
        "discovery": [],
        "triage": [],
        "pending_searches": [],
        "errors": [],
    }
    try:
        for municipality in municipalities:
            audit.emit("municipality_start", municipality_id=municipality.id)
            discovery = Discoverer(config, fetcher, gateway, audit)
            documents, records, gaps = [], [], []
            scanned = classified = 0
            for download in discovery.documents(municipality):
                try:
                    document = extract(download, municipality.id, run, config.extraction)
                    doc_meta = document.model_dump(mode="json", exclude={"text"})
                    documents.append(doc_meta)
                    data["documents"].append(doc_meta)
                    gaps.extend(document.warnings)
                    chunks, limited = chunk_document(document, config.extraction)
                    if limited:
                        gaps.append("chunk_limit:" + document.id)
                    for chunk in chunks:
                        scanned += 1
                        write_json(run / "chunks" / (digest(chunk.id) + ".json"), chunk.model_dump())
                        triage = {"municipality_id": municipality.id, "url": document.url, **triage_chunk(chunk)}
                        data["triage"].append(triage)
                        write_json(run / "triage" / (digest(chunk.id) + ".json"), triage)
                        audit.emit("triage", municipality_id=municipality.id, chunk_id=chunk.id, status=triage["status"])
                        if collect_only or triage["status"] == "irrelevant":
                            continue
                        classified += 1
                        record = classifier.classify(municipality, document, chunk)
                        records.append(record)
                        data["classifications"].append(record)
                        write_json(run / "units" / (digest(chunk.id) + ".json"), record)
                        for error in record["errors"]:
                            data["errors"].append({"municipality_id": municipality.id, "stage": "classification", "url": document.url, "detail": error})
                except Exception as error:
                    gaps.append("extraction_or_processing_failed")
                    entry = {"municipality_id": municipality.id, "stage": "processing", "url": download.url, "detail": f"{type(error).__name__}: {error}"}
                    data["errors"].append(entry)
                    audit.emit("processing_error", **{k: v for k, v in entry.items() if k != "stage"})
            data["discovery"].extend(discovery.events)
            for event in discovery.events:
                if event.get("outcome") == "error":
                    data["errors"].append({"municipality_id": municipality.id, "stage": event["method"], "url": event["url"], "detail": event["error"]})
            gaps.extend(discovery.gaps)
            if not documents:
                gaps.append("no_documents_retrieved")
            # Failed/limited searches stay pending. They are never translated to 'No'.
            for gap in sorted(set(gaps)):
                if any(token in gap for token in ["search", "download", "sitemap", "crawl", "document_limit", "no_documents"]):
                    data["pending_searches"].append({
                        "municipality_id": municipality.id,
                        "municipality": municipality.name,
                        "status": "pending",
                        "reason": gap,
                        "meeting_archives": municipality.meeting_archives,
                        "queries": config.discovery.queries,
                    })
            data["summary"].append(summarize(municipality, documents, records, gaps, config, collect_only, chunks_scanned=scanned, chunks_classified=classified))
            write_json(run / "results.json", data)
        audit.emit("run_finished", municipalities=len(municipalities), api_calls=gateway.calls)
        manifest.update(
            status="completed_with_gaps" if data["errors"] or any(s["needs_review"] for s in data["summary"]) else "completed",
            finished_at=utc_now(),
            api_calls=gateway.calls,
        )
        write_json(run / "manifest.json", manifest)
        export_workbook(run)
        manifest["artifacts"] = {
            p.relative_to(run).as_posix(): digest(p.read_bytes())
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name not in {"manifest.json", "pipeline.log"}
        }
        write_json(run / "manifest.json", manifest)
        return data
    except BaseException as error:
        manifest.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", error_type=type(error).__name__, finished_at=utc_now())
        write_json(run / "manifest.json", manifest)
        write_json(run / "results.json", data)
        raise
    finally:
        fetcher.close()
        gateway.close()
        logger.removeHandler(handler)
        handler.close()
