from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Label(StrictModel):
    definition: str
    requires_evidence: bool = True
    requires_before_cutoff: bool = False
    conclusive: bool = True


class Research(StrictModel):
    question: str
    cutoff: date | None = None
    instructions: str = ""
    labels: dict[str, Label]
    unknown_label: str
    fields: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_labels(self):
        if self.unknown_label not in self.labels:
            raise ValueError("unknown_label must be in labels")
        if self.labels[self.unknown_label].conclusive:
            raise ValueError("unknown_label must be non-conclusive")
        if "Review" in self.labels:
            raise ValueError("Review is reserved for unresolved municipality-level conflicts")
        if not self.cutoff and any(x.requires_before_cutoff for x in self.labels.values()):
            raise ValueError("Time-constrained labels need a cutoff")
        return self


class Discovery(StrictModel):
    provider: Literal["crawl", "openai", "hybrid"] = "hybrid"
    queries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    max_searches_per_municipality: int = Field(default=3, ge=0, le=50)
    max_documents: int = Field(default=20, ge=1, le=10000)
    max_depth: int = Field(default=2, ge=0, le=10)
    max_sitemaps: int = Field(default=8, ge=0, le=100)
    max_sitemap_urls: int = Field(default=10000, ge=1)


class Network(StrictModel):
    user_agent: str = "MunicipalResearch/0.1 (public research; configure contact in YAML)"
    timeout_seconds: float = Field(default=30, gt=0)
    interval_seconds: float = Field(default=1.0, ge=0)
    retries: int = Field(default=3, ge=0, le=10)
    max_bytes: int = Field(default=25000000, gt=0)
    max_redirects: int = Field(default=5, ge=0, le=10)
    respect_robots: bool = True


class Extraction(StrictModel):
    chunk_chars: int = Field(default=14000, ge=100)
    overlap_chars: int = Field(default=1500, ge=0)
    max_chunks_per_document: int = Field(default=100, ge=1)
    min_page_chars: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def overlap(self):
        if self.overlap_chars >= self.chunk_chars:
            raise ValueError("overlap_chars must be smaller than chunk_chars")
        return self


class LLM(StrictModel):
    models: list[str] = Field(default_factory=lambda: ["gpt-5-mini"], min_length=1)
    search_model: str = "gpt-5-mini"
    verifier_model: str = "gpt-5-mini"
    adjudicator_model: str = "gpt-5-mini"
    passes: int = Field(default=3, ge=1, le=10)
    max_output_tokens: int = Field(default=6000, ge=100)
    timeout_seconds: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    interval_seconds: float = Field(default=1, ge=0)
    max_calls: int = Field(default=1000, ge=1)
    reasoning_effort: str | None = None


class Config(StrictModel):
    research: Research
    discovery: Discovery = Field(default_factory=Discovery)
    network: Network = Field(default_factory=Network)
    extraction: Extraction = Field(default_factory=Extraction)
    llm: LLM = Field(default_factory=LLM)


class Municipality(StrictModel):
    id: str
    name: str
    domains: list[str]
    meeting_archives: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_input(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.id):
            raise ValueError("Municipality id must be a safe, unique ASCII identifier")
        if not self.domains:
            raise ValueError("At least one domain is required")
        self.domains = [x.lower().strip(".") for x in self.domains]
        for domain in self.domains:
            if not re.fullmatch(r"[a-zA-Z0-9.-]+", domain) or "." not in domain:
                raise ValueError("Use plain hostnames, without schemes, ports or wildcards")
        for url in self.meeting_archives + self.seeds:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid seed/archive URL: {url}")
        # Archive pages are priority discovery seeds, but remain separately identified
        # so coverage reports can say whether historical meeting material was searched.
        self.seeds = list(dict.fromkeys(self.meeting_archives + self.seeds))
        return self


def load_config(path: Path) -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_municipalities(path: Path) -> list[Municipality]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = [
        Municipality(
            id=r["id"],
            name=r["name"],
            domains=[v.strip() for v in r["domains"].split(";") if v.strip()],
            meeting_archives=[
                v.strip() for v in r.get("meeting_archives", "").split(";") if v.strip()
            ],
            seeds=[v.strip() for v in r.get("seeds", "").split(";") if v.strip()],
        )
        for r in rows
    ]
    if not result or len({r.id for r in result}) != len(result):
        raise ValueError("Municipality CSV must be nonempty and have unique ids")
    return result
