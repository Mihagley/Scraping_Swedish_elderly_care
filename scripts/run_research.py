"""Run one bounded research request locally or in GitHub Actions."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from municipal_research.config import StrictModel, load_config, load_municipalities
from municipal_research.pipeline import run_pipeline
from municipal_research.storage import write_json


class ResearchRequest(StrictModel):
    request_id: str
    mode: str = "collect"
    config: str = "examples/language_requirements.yaml"
    municipalities: str = "research/municipalities-pilot.csv"
    limit: int = Field(default=6, ge=1, le=290)
    max_documents: int = Field(default=1, ge=1, le=100)
    max_chunks_per_document: int = Field(default=20, ge=1, le=100)
    max_api_calls: int = Field(default=200, ge=1, le=10000)


def input_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Research input must be an existing file within the repository")
    return path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    request = ResearchRequest.model_validate_json(
        (root / "research/request.json").read_text(encoding="utf-8")
    )
    mode = os.getenv("RESEARCH_MODE") or request.mode
    if mode not in {"collect", "classify"}:
        raise ValueError("Mode must be collect or classify")
    if mode == "classify" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "Add the repository Actions secret OPENAI_API_KEY, then rerun in classify mode. Never put the key in a committed file."
        )
    config = load_config(input_path(root, request.config))
    municipalities = load_municipalities(input_path(root, request.municipalities))[: request.limit]
    # This pilot deliberately examines targeted candidates rather than spending API
    # calls rediscovering known URLs. The general CLI retains full hybrid discovery.
    config.discovery.provider = "crawl"
    config.discovery.max_sitemaps = 0
    config.discovery.max_depth = 0
    config.discovery.max_documents = request.max_documents
    config.extraction.max_chunks_per_document = request.max_chunks_per_document
    config.llm.max_calls = request.max_api_calls
    config.network.user_agent = (
        "MunicipalResearch/0.1 (+https://github.com/Mihagley/Scraping_Swedish_elderly_care)"
    )
    run_id = os.getenv("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime(
        "local-%Y%m%dT%H%M%SZ"
    )
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    name = f"{run_id}-{attempt}-{mode}"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid run identifier")
    run = root / "research/results" / name
    result = run_pipeline(
        config, municipalities, run, root / "data/cache", collect_only=mode == "collect"
    )
    summary = {
        "run_directory": run.relative_to(root).as_posix(),
        "mode": mode,
        "request": request.model_dump(),
        "municipalities": result["summary"],
        "errors": len(result["errors"]),
        "github_commit": os.getenv("GITHUB_SHA"),
    }
    write_json(root / "research/latest.json", summary)
    lines = [
        "# Municipal research pilot",
        "",
        f"Mode: **{mode}**. Results: `{summary['run_directory']}`.",
        "",
        "| Municipality | Documents | Category | Review needed |",
        "|---|---:|---|---|",
    ]
    for row in result["summary"]:
        lines.append(
            f"| {row['municipality']} | {row['documents']} | {row['category']} | {row['needs_review']} |"
        )
    lines += [
        "",
        "Collection-only runs have no substantive classifications. Coverage is bounded; inspect Errors and Evidence before interpreting a finding.",
    ]
    report = "\n".join(lines) + "\n"
    (root / "research/STATUS.md").write_text(report, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(report)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not any(row["documents"] for row in result["summary"]):
        raise RuntimeError("No source documents were retrieved; see the preserved error audit")


if __name__ == "__main__":
    main()
