from __future__ import annotations

from typing import Literal

from pydantic import Field, create_model

from .config import Research, StrictModel


class Quote(StrictModel):
    text: str = Field(description="A verbatim, contiguous passage copied from this chunk")
    purpose: Literal["finding", "timing", "scope", "attribute"]


class Attribute(StrictModel):
    name: str
    value: str
    quote_indices: list[int] = Field(description="Zero-based indices into evidence")


class Decision(StrictModel):
    category: str
    rationale: str = Field(description="Brief evidence-based justification, not hidden reasoning")
    temporal_relation: Literal["before_cutoff", "at_or_after_cutoff", "unknown", "not_applicable"]
    publication_date: str | None = Field(
        default=None, description="Source publication date, if explicit"
    )
    decision_date: str | None = Field(
        default=None, description="Formal political/administrative decision date, if explicit"
    )
    implementation_date: str | None = Field(
        default=None, description="Date implementation started, if explicit"
    )
    in_force_by_date: str | None = Field(
        default=None,
        description="Latest explicit date by which the requirement is shown already in force",
    )
    # Retained for backwards compatibility with earlier pilot outputs. National runs
    # use the four semantically distinct fields above and normally leave this null.
    effective_date: str | None = Field(default=None, description="Legacy generic effective date")
    scope: str | None
    attributes: list[Attribute]
    evidence: list[Quote]


class Verdict(StrictModel):
    supported: bool
    issues: list[str]
    explanation: str


def decision_schema(research: Research) -> type[Decision]:
    labels = Literal[tuple(research.labels)]
    return create_model("ResearchDecision", __base__=Decision, category=(labels, ...))


class Page(StrictModel):
    page: int | None
    start: int
    end: int


class Document(StrictModel):
    id: str
    municipality_id: str
    url: str
    requested_url: str
    retrieved_at: str
    raw_sha256: str
    text_sha256: str
    raw_path: str
    text_path: str
    numbered_path: str
    media_type: str
    title: str
    text: str
    pages: list[Page]
    warnings: list[str]
    extractor: str


class Chunk(StrictModel):
    id: str
    document_id: str
    start: int
    end: int
    text: str


class Location(StrictModel):
    match: Literal["exact", "whitespace"]
    start: int
    end: int
    line_start: int
    line_end: int
    page_start: int | None
    page_end: int | None
    page_char_start: int | None
    page_char_end: int | None
    matched_text: str


class QuoteCheck(StrictModel):
    quote_index: int
    quote: str
    purpose: str
    verified: bool
    locations: list[Location]
    issue: str | None
