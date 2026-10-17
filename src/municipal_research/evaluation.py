"""Small source-frozen evaluation. Reference labels never enter inference payloads.

No model benchmarks run automatically. Preparing a packet performs no network work.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from .budget import Budget, BudgetExhausted
from .classification import Classifier, abstention
from .config import Config, Municipality
from .llm import Gateway
from .models import Chunk, Document, Quote
from .storage import Audit, digest, read_json, write_json
from .verification import verify_decision


def prepare(run: Path, output: Path, limit=10):
    if not 1 <= limit <= 20:
        raise ValueError("Choose 1–20 saved text chunks")
    if output.exists():
        raise ValueError("Choose a new packet path")
    data = read_json(run / "results.json")
    manifest = read_json(run / "manifest.json")
    if not manifest.get("artifacts"):
        raise ValueError("Use a sealed run with verifiable source hashes")
    for name, expected in manifest["artifacts"].items():
        if digest((run / name).read_bytes()) != expected:
            raise ValueError("Source run was modified: " + name)
    organisations = {item["id"]: item for item in read_json(run / "municipalities.json")}
    documents = {item["id"]: item for item in data["documents"]}
    items = []
    for path in sorted((run / "chunks").glob("*.json"))[:limit]:
        chunk = read_json(path)
        meta = documents[chunk["document_id"]]
        text = (run / meta["text_path"]).read_text(encoding="utf-8")
        if digest(text) != meta["text_sha256"]:
            raise ValueError("Extracted text integrity failure")
        items.append(
            {
                "id": chunk["id"],
                "chunk": chunk,
                "document": {**meta, "text": text},
                "location": organisations[meta["municipality_id"]],
            }
        )
    if not items:
        raise ValueError("No saved text chunks available")
    packet = {
        "version": 1,
        "config": data["config"],
        "source_manifest_sha256": digest((run / "manifest.json").read_bytes()),
        "retrieved_urls": sorted({item["url"] for item in data["documents"]}),
        "items": items,
    }
    write_json(output, packet)
    # A blank template is not a gold standard. Keep it OUTSIDE the inference packet.
    write_json(
        output.with_name(output.stem + ".reference-template.json"),
        {
            "provenance": "",
            "annotator": "",
            "checked_by": "",
            "blind_to_suggestions": False,
            "relevant_urls": [],
            "items": [
                {
                    "id": item["id"],
                    "category": None,
                    "effective_date": None,
                    "scope": None,
                    "provenance": "",
                }
                for item in items
            ],
        },
    )
    return packet


def rule_decision(config, document, chunk, pattern=None, label=None):
    decision = abstention(config, "Rules-only baseline: no supported explicit rule match.")
    if pattern and label:
        if label not in config.research.labels:
            raise ValueError("Rule label must belong to the codebook")
        match = re.search(re.escape(pattern), chunk.text, re.I)
        if match and len(match.group()) >= 12:
            decision.category = label
            decision.rationale = "User-defined literal rule match; semantic human review required."
            decision.temporal_relation = "not_applicable"
            decision.evidence = [Quote(text=match.group(), purpose="finding")]
            _, issues = verify_decision(decision, document, chunk, config.research)
            if issues:
                decision = abstention(config, "Literal rule failed evidence/time/scope checks.")
    return decision


def run_comparison(
    packet_path, output, engine, max_calls, seconds, model=None, pattern=None, label=None
):
    packet = read_json(packet_path)
    if not 1 <= len(packet["items"]) <= 20 or not 1 <= max_calls <= 100 or not 1 <= seconds <= 1800:
        raise ValueError("Evaluation bounds: 1–20 chunks, 1–100 model calls, 1–1800 seconds")
    if output.exists():
        raise ValueError("Choose a new evaluation run directory")
    config = Config.model_validate(packet["config"])
    config.limits.run_seconds = config.limits.organisation_seconds = seconds
    config.limits.run_requests = max_calls
    config.llm.max_calls = config.llm.max_calls_per_organisation = max_calls
    if engine != "rules":
        if not model:
            raise ValueError("Explicit model name required; no default paid benchmark")
        config.llm.provider = engine
        config.llm.models = [model]
        config.llm.verifier_model = config.llm.adjudicator_model = model
        if engine == "ollama":
            config.llm.reasoning_effort = None
        config = Config.model_validate(config.model_dump(mode="json"))
    output.mkdir(parents=True)
    audit = Audit(output / "audit.jsonl")
    budget = Budget(config.limits, audit=audit)
    budget.begin("frozen-source-evaluation")
    gateway = Gateway(config.llm, output / "cache", output, audit, budget=budget)
    classifier = Classifier(config, gateway, audit)
    started = time.monotonic()
    report = {
        "engine": engine,
        "model": model,
        "packet_sha256": digest(packet_path.read_bytes()),
        "source_manifest_sha256": packet["source_manifest_sha256"],
        "items": [],
        "unprocessed": [item["id"] for item in packet["items"]],
        "stop_reason": None,
    }
    try:
        for item in packet["items"]:
            budget.check()
            document = Document.model_validate(item["document"])
            chunk = Chunk.model_validate(item["chunk"])
            if (
                chunk.text != document.text[chunk.start : chunk.end]
                or digest(document.text) != document.text_sha256
            ):
                raise ValueError("Packet source/offset integrity failure")
            if engine == "rules":
                decision = rule_decision(config, document, chunk, pattern, label)
                checks, issues = verify_decision(decision, document, chunk, config.research)
                result = {
                    "decision": decision.model_dump(mode="json"),
                    "quotes": [check.model_dump(mode="json") for check in checks],
                    "needs_review": True,
                    "mechanical_issues": issues,
                }
            else:
                result = classifier.classify(
                    Municipality.model_validate(item["location"]), document, chunk
                )
            report["items"].append({"id": item["id"], **result})
            report["unprocessed"].remove(item["id"])
            write_json(output / "predictions.json", report)
    except BudgetExhausted as error:
        report["stop_reason"] = str(error)
    finally:
        report.update(
            runtime_seconds=time.monotonic() - started,
            calls=gateway.calls,
            local_calls=gateway.local_calls,
            paid_calls=gateway.paid_calls,
            budget=budget.snapshot(),
        )
        write_json(output / "predictions.json", report)
        gateway.close()
    return report


def score(packet_path, predictions_path, reference_path):
    packet, predictions, reference = map(read_json, (packet_path, predictions_path, reference_path))
    if predictions["packet_sha256"] != digest(packet_path.read_bytes()):
        raise ValueError("Predictions came from a different source packet")
    if (
        not reference.get("blind_to_suggestions")
        or not reference.get("provenance")
        or not reference.get("annotator")
        or not reference.get("checked_by")
        or reference["annotator"] == reference["checked_by"]
    ):
        raise ValueError(
            "Independent, blind, checked reference labels with provenance are required; blank templates/model output are not gold labels"
        )
    gold = {item["id"]: item for item in reference["items"]}
    if any(
        item["id"] not in gold
        or not gold[item["id"]].get("provenance")
        or not gold[item["id"]].get("category")
        for item in packet["items"]
    ):
        raise ValueError("Every packet item needs checked reference provenance and a category")
    unknown = packet["config"]["research"]["unknown_label"]
    predicted = {item["id"]: item for item in predictions["items"]}
    metrics = {}
    for category in packet["config"]["research"]["labels"]:
        tp = fp = fn = 0
        for item in packet["items"]:
            expected = gold[item["id"]]["category"]
            actual = predicted.get(item["id"], {}).get("decision", {}).get("category", unknown)
            tp += actual == expected == category
            fp += actual == category and expected != category
            fn += expected == category and actual != category
        metrics[category] = {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    if any(
        item["category"] not in packet["config"]["research"]["labels"] for item in gold.values()
    ):
        raise ValueError("Reference categories must belong to the codebook")
    relevant = set(reference.get("relevant_urls", []))
    retrieved = set(packet["retrieved_urls"])
    rows = predictions["items"]
    checks = {
        field: {
            "correct": sum(
                row["decision"].get(field) == gold[row["id"]].get(field) for row in rows
            ),
            "evaluated": len(rows),
        }
        for field in ("effective_date", "scope")
    }
    mechanical_failures = sum(
        any(not quote.get("verified") for quote in row.get("quotes", [])) for row in rows
    )
    # Semantic unsupported claims require a separate human audit of predictions.
    human_audit = reference.get("prediction_audits", {}).get(digest(predictions_path.read_bytes()))
    unsupported = None
    if human_audit and human_audit.get("reviewer") and human_audit.get("provenance"):
        audited = human_audit.get("items", [])
        if {item.get("id") for item in audited} != set(predicted):
            raise ValueError("Human unsupported-claim audit must cover every prediction")
        unsupported = sum(bool(item["unsupported_claim"]) for item in audited)
    return {
        "classification_by_category": metrics,
        "retrieval_recall_within_human_reference_set": len(relevant & retrieved) / len(relevant)
        if relevant
        else None,
        "retrieval_reference_count": len(relevant),
        "retrieval_note": "Reference-set recall, not nationwide/web-wide recall; null without independently enumerated relevant URLs.",
        "date_scope_accuracy": checks,
        "unsupported_claims_human_audit": unsupported,
        "mechanical_quote_failures": mechanical_failures,
        "abstentions": sum(row["decision"]["category"] == unknown for row in rows),
        "unprocessed": predictions["unprocessed"],
        "runtime_seconds": predictions["runtime_seconds"],
        "calls": predictions["calls"],
        "local_calls": predictions["local_calls"],
        "paid_calls": predictions["paid_calls"],
        "validation_note": "Reference attestations are supplied by humans, not verified by software. Null unsupported-claim counts mean not human-audited, never zero.",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Bounded evaluation on identical frozen sources; no automatic benchmarks"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    packet = sub.add_parser("prepare")
    packet.add_argument("run", type=Path)
    packet.add_argument("output", type=Path)
    packet.add_argument("--limit", type=int, default=10)
    run = sub.add_parser("run")
    run.add_argument("packet", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--engine", choices=["rules", "ollama", "openai"], required=True)
    run.add_argument("--max-calls", type=int, required=True)
    run.add_argument("--seconds", type=int, required=True)
    run.add_argument("--model")
    run.add_argument("--rule-pattern")
    run.add_argument("--rule-label")
    report = sub.add_parser("score")
    report.add_argument("packet", type=Path)
    report.add_argument("predictions", type=Path)
    report.add_argument("reference", type=Path)
    report.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.run, args.output, args.limit)
    elif args.command == "run":
        run_comparison(
            args.packet,
            args.output,
            args.engine,
            args.max_calls,
            args.seconds,
            args.model,
            args.rule_pattern,
            args.rule_label,
        )
    else:
        if args.output.exists():
            raise ValueError("Choose a new score output file")
        write_json(args.output, score(args.packet, args.predictions, args.reference))


if __name__ == "__main__":
    main()
