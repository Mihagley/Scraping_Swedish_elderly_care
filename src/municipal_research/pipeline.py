from __future__ import annotations

import logging
import shutil
from pathlib import Path

from . import __version__
from .budget import Budget, BudgetExhausted, bounded_call
from .classification import Classifier
from .config import Config, Municipality
from .discovery import Discoverer
from .export import export_workbook
from .extraction import chunk_document, extract
from .llm import Gateway
from .network import Fetcher
from .storage import Audit, atomic_bytes, canonical, digest, environment, read_json, utc_now, write_json


def summarize(
    municipality: Municipality,
    documents: list[dict],
    records: list[dict],
    gaps: list[str],
    config: Config,
    collect_only=False,
) -> dict:
    accepted = [r for r in records if config.research.labels[r["decision"]["category"]].conclusive]
    labels = {r["decision"]["category"] for r in accepted}
    conflicts = []
    # Missing data is not disagreement. Different explicit values remain a review item.
    for key in ["effective_date", "scope"]:
        if len({r["decision"][key] for r in accepted if r["decision"][key]}) > 1:
            conflicts.append(key)
    for key in config.research.fields:
        values = {
            a["value"] for r in accepted for a in r["decision"]["attributes"] if a["name"] == key
        }
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
    needs_review = (
        collect_only
        or not labels
        or len(labels) > 1
        or bool(gaps)
        or bool(conflicts)
        or any(r["needs_review"] for r in records)
    )
    return {
        "municipality_id": municipality.id,
        "municipality": municipality.name,
        "location_type": municipality.kind,
        "category": category,
        "needs_review": needs_review,
        "documents": len(documents),
        "chunks": len(records),
        "verified_quotes": sum(q["verified"] for r in records for q in r["quotes"]),
        "coverage": "bounded_with_gaps" if gaps else "bounded",
        "finding_status": "evidence_found" if labels else "not_found_within_configured_limits",
        "gaps": sorted(set(gaps)),
        "source_urls": sorted({d["url"] for d in documents}),
        "effective_dates": sorted(
            {r["decision"]["effective_date"] for r in accepted if r["decision"]["effective_date"]}
        ),
        "scopes": sorted({r["decision"]["scope"] for r in accepted if r["decision"]["scope"]}),
        **{
            f"field:{key}": sorted(
                {
                    a["value"]
                    for r in accepted
                    for a in r["decision"]["attributes"]
                    if a["name"] == key
                }
            )
            for key in config.research.fields
        },
    }


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
    resume_from: Path | None = None,
    cancel_file: Path | None = None,
) -> dict:
    if run.exists() and any(run.iterdir()):
        raise ValueError("Run directory is not empty; choose a new directory (cache is reusable)")
    if offline and refresh:
        raise ValueError("--offline and --refresh are mutually exclusive")
    if cache.resolve().is_relative_to(run.resolve()):
        raise ValueError("Keep the shared cache outside the immutable run directory")
    previous = None
    if resume_from:
        if offline or refresh:
            raise ValueError("Resume is separate from immutable offline replay and fresh-source refresh")
        previous = read_json(resume_from / "results.json")
        old_config = Config.model_validate(previous["config"])
        # A continuation may change explicit budgets, not the investigation/codebook.
        def identity(value):
            value = value.model_dump(mode="json")
            value.pop("limits", None)
            value["discovery"].pop("max_documents", None)
            for key in ("max_calls", "max_calls_per_organisation", "timeout_seconds"):
                value["llm"].pop(key, None)
            return value
        if identity(old_config) != identity(config) or read_json(resume_from / "municipalities.json") != [m.model_dump() for m in municipalities]:
            raise ValueError("Resume must preserve sources, question, codebook and analysis settings; only budgets may change")
        if read_json(resume_from / "manifest.json")["collect_only"] != collect_only:
            raise ValueError("Resume must preserve collection/analysis mode")
        manifest_old = read_json(resume_from / "manifest.json")
        if cache.resolve() != Path(manifest_old["cache_dir"]).resolve():
            raise ValueError("Resume must use the original cache; use a separate fresh-source run to change sources")
        for name, expected in manifest_old.get("artifacts", {}).items():
            if digest((resume_from / name).read_bytes()) != expected:
                raise ValueError("Resume source integrity check failed: " + name)
    run.mkdir(parents=True, exist_ok=True)
    if resume_from:
        for name in ("sources", "chunks", "units", "llm", "checkpoints"):
            if (resume_from / name).exists():
                shutil.copytree(resume_from / name, run / name)

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
    budget = Budget(config.limits, audit=audit, cancel_file=cancel_file)
    fetcher = (fetcher_factory or Fetcher)(
        config.network, cache / "http", audit, offline=offline, refresh=refresh, budget=budget
    )
    gateway = (gateway_factory or Gateway)(
        config.llm, cache / "llm", run, audit, offline=offline, refresh=refresh, budget=budget
    )
    classifier = Classifier(config, gateway, audit)
    data = {
        "config": config_data,
        "summary": [],
        "documents": [],
        "classifications": [],
        "discovery": [],
        "errors": [],
    }
    if previous:
        for key in ("summary", "documents", "classifications", "discovery", "errors"):
            data[key] = previous[key]
        manifest["resume_from"] = str(resume_from.resolve())
        manifest["parent_manifest_sha256"] = digest((resume_from / "manifest.json").read_bytes())
    data["progress"] = {"stopping_reasons": [], "unprocessed_organisations": [m.id for m in municipalities], "completed_organisations": [], "incomplete_organisations": [], "resumed_from": str(resume_from) if resume_from else None}
    run_stopped = False
    write_json(run / "results.json", data)
    try:
        for municipality in municipalities:
            budget.begin(municipality.id)
            gateway.organisation_calls = 0
            checkpoint_path = run / "checkpoints" / (municipality.id + ".json")
            checkpoint = read_json(checkpoint_path) if previous and checkpoint_path.exists() else None
            if checkpoint and not checkpoint["queue"] and not checkpoint.get("inflight"):
                data["progress"]["completed_organisations"].append(municipality.id)
                data["progress"]["unprocessed_organisations"].remove(municipality.id)
                continue
            audit.emit("municipality_start", municipality_id=municipality.id)
            discovery = Discoverer(config, fetcher, gateway, audit, checkpoint=checkpoint)
            documents = [d for d in data["documents"] if d["municipality_id"] == municipality.id]
            records = [r for r in data["classifications"] if r["municipality_id"] == municipality.id]
            gaps = [w for d in documents for w in d.get("warnings", [])]
            stopping = None
            try:
                budget.check()
                for download in discovery.documents(municipality):
                    try:
                        budget.check()
                        # Preserve raw bytes before entering the parser subprocess.
                        source_id = digest(municipality.id + "\n" + download.url + "\n" + download.sha256)[:24]
                        source_path = run / "sources" / source_id / ("source.pdf" if download.body.lstrip().startswith(b"%PDF-") else "source.json" if "json" in download.content_type.lower() else "source.html")
                        atomic_bytes(source_path, download.body)
                        write_json(source_path.parent / "download.json", {"url": download.url, "requested_url": download.requested_url, "sha256": download.sha256, "retrieved_at": download.retrieved_at, "content_type": download.content_type, "raw_path": str(source_path.relative_to(run))})
                        document = bounded_call(extract, (download, municipality.id, run, config.extraction), budget.timeout(config.limits.extraction_seconds), check=budget.check)
                        budget.check()
                        doc_meta = document.model_dump(mode="json", exclude={"text"})
                        existing = {d["id"] for d in documents}
                        if document.id not in existing:
                            documents.append(doc_meta)
                            data["documents"].append(doc_meta)
                        navigation = document.navigation_page
                        if navigation:
                            budget.count("navigation_pages")
                        usable = len(document.text) >= config.extraction.min_page_chars and not any(x in document.warnings for x in ("suspected_error_page_not_classified", "javascript_only_suspected"))
                        if usable:
                            budget.count("usable_documents")
                        write_json(run / "results.json", data)
                        gaps.extend(document.warnings)
                        chunks, limited = chunk_document(document, config.extraction)
                        if limited:
                            gaps.append("chunk_limit:" + document.id)
                        if not usable:
                            chunks = []
                        for chunk in chunks:
                            budget.check()
                            if any(r["chunk_id"] == chunk.id for r in records):
                                continue
                            write_json(
                                run / "chunks" / (digest(chunk.id) + ".json"), chunk.model_dump()
                            )
                            if collect_only:
                                continue
                            record = classifier.classify(municipality, document, chunk)
                            records.append(record)
                            data["classifications"].append(record)
                            write_json(run / "units" / (digest(chunk.id) + ".json"), record)
                            write_json(run / "results.json", data)
                            for error in record["errors"]:
                                data["errors"].append(
                                    {
                                        "municipality_id": municipality.id,
                                        "stage": "classification",
                                        "url": document.url,
                                        "detail": error,
                                    }
                                )
                    except BudgetExhausted:
                        raise
                    except Exception as error:
                        budget.check()
                        gaps.append("extraction_or_processing_failed")
                        entry = {
                            "municipality_id": municipality.id,
                            "stage": "processing",
                            "url": download.url,
                            "detail": f"{type(error).__name__}: {error}",
                        }
                        data["errors"].append(entry)
                        audit.emit(
                            "processing_error", **{k: v for k, v in entry.items() if k != "stage"}
                        )
            except BudgetExhausted as error:
                stopping = {"municipality_id": municipality.id, "scope": error.scope, "reason": error.reason}
                gaps.append(str(error))
                data["progress"]["stopping_reasons"].append(stopping)
                audit.emit("budget_exhausted", **stopping)
                run_stopped = error.scope == "run"
            data["discovery"].extend(discovery.events)
            for event in discovery.events:
                if event.get("outcome") == "error":
                    data["errors"].append(
                        {
                            "municipality_id": municipality.id,
                            "stage": event["method"],
                            "url": event["url"],
                            "detail": event["error"],
                        }
                    )
            gaps.extend(discovery.gaps)
            if not documents:
                gaps.append("no_documents_retrieved")
            data["summary"] = [row for row in data["summary"] if row["municipality_id"] != municipality.id]
            row = summarize(municipality, documents, records, gaps, config, collect_only)
            row.update(counts=dict(budget.current), stop_reason=stopping, remaining=budget.snapshot())
            if checkpoint:
                row["counts_scope"] = "this_explicit_continuation_only; documents/classifications are cumulative"
            data["summary"].append(row)
            data["progress"]["unprocessed_organisations"].remove(municipality.id)
            if stopping is None and not discovery.queue:
                data["progress"]["completed_organisations"].append(municipality.id)
            else:
                data["progress"]["incomplete_organisations"].append(municipality.id)
            data["budget"] = budget.snapshot()

            write_json(run / "results.json", data)
            if run_stopped:
                break
        audit.emit("run_finished", municipalities=len(municipalities), api_calls=gateway.calls)
        manifest.update(
            status="stopped_with_partial_results" if data["progress"]["stopping_reasons"] else "completed_with_gaps"
            if data["errors"] or any(s["needs_review"] for s in data["summary"])
            else "completed",
            finished_at=utc_now(),
            api_calls=gateway.calls,
            local_calls=gateway.local_calls,
            paid_calls=gateway.paid_calls,
            progress=data["progress"],
            budget=budget.snapshot(),
        )
        data["budget"] = budget.snapshot()
        write_json(run / "results.json", data)
        write_json(run / "manifest.json", manifest)
        export_workbook(run)
        # Hash the durable run artifacts; the manifest excludes itself by definition.
        manifest["artifacts"] = {
            p.relative_to(run).as_posix(): digest(p.read_bytes())
            for p in sorted(run.rglob("*"))
            if p.is_file() and p.name not in {"manifest.json", "pipeline.log"}
        }
        write_json(run / "manifest.json", manifest)
        return data
    except BaseException as error:
        manifest.update(
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
            error_type=type(error).__name__,
            finished_at=utc_now(),
        )
        write_json(run / "manifest.json", manifest)
        write_json(run / "results.json", data)
        raise
    finally:
        fetcher.close()
        gateway.close()
        logger.removeHandler(handler)
        handler.close()
