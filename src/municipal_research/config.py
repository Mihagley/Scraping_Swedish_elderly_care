from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

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
    max_documents: int = Field(default=5, ge=1, le=10000)
    max_depth: int = Field(default=2, ge=0, le=10)
    max_sitemaps: int = Field(default=8, ge=0, le=100)
    max_sitemap_urls: int = Field(default=10000, ge=1, le=100000)


class Limits(StrictModel):
    run_requests: int = Field(default=500, ge=1, le=100000)
    organisation_requests: int = Field(default=40, ge=1, le=10000)
    run_seconds: float = Field(default=1800, gt=0, le=86400)
    organisation_seconds: float = Field(default=180, gt=0, le=3600)
    extraction_seconds: float = Field(default=20, gt=0, le=120)


class Network(StrictModel):
    user_agent: str = "MunicipalResearch/0.1 (public research; configure contact in YAML)"
    timeout_seconds: float = Field(default=30, gt=0)
    interval_seconds: float = Field(default=1.0, ge=0)
    retries: int = Field(default=3, ge=0, le=10)
    max_bytes: int = Field(default=25000000, gt=0)
    max_redirects: int = Field(default=5, ge=0, le=10)
    respect_robots: bool = True


class Extraction(StrictModel):
    max_pdf_pages: int = Field(default=100, ge=1, le=2000)
    chunk_chars: int = Field(default=14000, ge=100)
    overlap_chars: int = Field(default=1500, ge=0)
    max_chunks_per_document: int = Field(default=100, ge=1, le=1000)
    min_page_chars: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def overlap(self):
        if self.overlap_chars >= self.chunk_chars:
            raise ValueError("overlap_chars must be smaller than chunk_chars")
        return self


class LLM(StrictModel):
    max_calls_per_organisation: int = Field(default=20, ge=1)
    analysis_mode: Literal["verified", "light"] = "verified"
    adjudicate: bool = True
    input_tokens: int = Field(default=24000, ge=256, le=200000)
    provider: Literal["openai", "ollama"] = "openai"
    ollama_base_url: str = "http://localhost:11434"
    ollama_num_ctx: int = Field(default=32768, ge=2048, le=262144)
    models: list[str] = Field(default_factory=lambda: ["gpt-5-mini"], min_length=1)
    search_model: str = "gpt-5-mini"
    verifier_model: str = "gpt-5-mini"
    adjudicator_model: str = "gpt-5-mini"
    passes: int = Field(default=3, ge=1, le=10)
    max_output_tokens: int = Field(default=6000, ge=100, le=65536)
    timeout_seconds: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    interval_seconds: float = Field(default=1, ge=0)
    max_calls: int = Field(default=1000, ge=1)
    reasoning_effort: str | None = None

    @model_validator(mode="after")
    def local_settings(self):
        url = urlsplit(self.ollama_base_url)
        if (
            url.scheme not in {"http", "https"}
            or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError(
                "Ollama requires a localhost origin, e.g. http://localhost:11434, without credentials or an API path"
            )
        try:
            _ = url.port
        except ValueError as error:
            raise ValueError("Ollama port is invalid") from error
        self.ollama_base_url = self.ollama_base_url.rstrip("/")
        if self.provider == "ollama":
            names = [*self.models, self.verifier_model, self.adjudicator_model]
            if any(not name.strip() or name != name.strip() for name in names):
                raise ValueError(
                    "Ollama model names must be nonempty and have no surrounding whitespace"
                )
            if any(name.lower().endswith((":cloud", "-cloud")) for name in names):
                raise ValueError("Choose a downloaded local Ollama model, not a cloud model")
            if self.max_output_tokens >= self.ollama_num_ctx:
                raise ValueError("Ollama context must exceed the maximum output tokens")
            if self.reasoning_effort:
                raise ValueError("reasoning_effort is an OpenAI-only option")
        return self


class Config(StrictModel):
    research: Research
    limits: Limits = Field(default_factory=Limits)
    discovery: Discovery = Field(default_factory=Discovery)
    network: Network = Field(default_factory=Network)
    extraction: Extraction = Field(default_factory=Extraction)
    llm: LLM = Field(default_factory=LLM)


class Municipality(StrictModel):
    # Legacy name retained for CLI and saved-run compatibility.
    kind: Literal["kommun", "region"] = "kommun"
    id: str
    name: str
    domains: list[str]
    seeds: list[str] = Field(default_factory=list)
    portal_hosts: list[str] = Field(default_factory=list)
    public_endpoints: list[str] = Field(default_factory=list)

    @property
    def allowed_domains(self):
        return self.domains + self.portal_hosts

    @model_validator(mode="after")
    def validate_input(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.id):
            raise ValueError("Location id must be a safe, unique ASCII identifier")
        if not self.domains:
            raise ValueError("At least one domain is required")
        for domain in self.domains + self.portal_hosts:
            if not re.fullmatch(r"[a-zA-Z0-9.-]+", domain) or "." not in domain:
                raise ValueError("Use plain hostnames, without schemes, ports or wildcards")
        self.domains = [x.lower().strip(".") for x in self.domains]
        self.portal_hosts = [x.lower().strip(".") for x in self.portal_hosts]
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
            kind=r.get("kind") or "kommun",
            domains=[v.strip() for v in r["domains"].split(";") if v.strip()],
            seeds=[v.strip() for v in r.get("seeds", "").split(";") if v.strip()],
            portal_hosts=[v.strip() for v in (r.get("portal_hosts") or "").split(";") if v.strip()],
            public_endpoints=[v.strip() for v in (r.get("public_endpoints") or "").split(";") if v.strip()],
        )
        for r in rows
    ]
    if not result or len({r.id for r in result}) != len(result):
        raise ValueError("Municipality CSV must be nonempty and have unique ids")
    return result


# New generic names; existing imports and CSVs remain supported.
Location = Municipality
load_locations = load_municipalities
