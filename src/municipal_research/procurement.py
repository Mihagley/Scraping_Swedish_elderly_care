"""Normalization and transparent language screening for procurement notices.

The module deliberately keeps missing documents and source coverage separate from
the language result. A missing attachment is not evidence that a tender had no
Swedish requirement.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class LanguageFinding:
    category: str
    evidence: str = ""
    confidence: str = "none"


@dataclass(frozen=True)
class ProcurementNotice:
    source: str
    notice_id: str
    year: int | None
    publication_date: str
    buyer_name: str
    municipality_name: str
    title: str
    document_status: str
    language_category: str
    language_evidence: str
    language_confidence: str
    source_url: str = ""
    document_url: str = ""
    coverage_status: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EXPLICIT = (
    r"\bkrav(?:et)?\s+(?:på|om)\s+(?:det )?svenska\b",
    r"\bgod svenska\b",
    r"\bsvenska\s+i\s+(?:tal|tal och skrift|skrift)\b",
    r"\bsvenska\s+språket\b",
    r"\byrkessvenska\b",
    r"\b(?:svenska|sva)\s*(?:1|2|3)\b",
    r"\b(?:cefr|gemsam europeisk referensram)[^\n]{0,30}\b(?:b2|c1)\b",
)
_RELATED = (r"\bspråkombud\b", r"\btolk(?:ning|)\b", r"\bspråkutbildning\b")


def classify_language_requirement(text: str | None) -> LanguageFinding:
    """Classify only what is stated in the supplied notice/document text."""
    if not text or not text.strip():
        return LanguageFinding("missing_document")
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    for pattern in _EXPLICIT:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            start, end = max(0, match.start() - 100), min(len(text), match.end() + 140)
            return LanguageFinding("explicit_requirement", text[start:end].strip(), "high")
    for pattern in _RELATED:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            start, end = max(0, match.start() - 100), min(len(text), match.end() + 140)
            return LanguageFinding("language_related", text[start:end].strip(), "medium")
    return LanguageFinding("no_evidence", "", "high")


def _first(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_notice(row: dict[str, Any], *, source: str) -> ProcurementNotice:
    """Map UHM/TED exports (or a user CSV) into the common review schema."""
    date = _first(row, "publication_date", "publication-date", "date", "published")
    year = None
    match = re.search(r"\b(20\d{2})\b", date)
    if match:
        year = int(match.group(1))
    title = _first(row, "title", "notice_title", "notice-title")
    text = _first(row, "document_text", "text", "description", "content", "document")
    finding = classify_language_requirement(text)
    document_url = _first(row, "document_url", "document-url", "document-url-part", "url")
    return ProcurementNotice(
        source=source,
        notice_id=_first(row, "notice_id", "publication_number", "publication-number", "id"),
        year=year,
        publication_date=date,
        buyer_name=_first(row, "buyer_name", "buyer-name", "contracting_authority"),
        municipality_name=_first(row, "municipality_name", "municipality", "buyer_municipality"),
        title=title,
        document_status="available" if text else "missing",
        language_category=finding.category,
        language_evidence=finding.evidence,
        language_confidence=finding.confidence,
        source_url=_first(row, "source_url", "source", "notice_url"),
        document_url=document_url,
        coverage_status=_first(row, "coverage_status", "coverage") or "unknown",
    )


def aggregate_by_year(notices: Iterable[ProcurementNotice]) -> list[dict[str, Any]]:
    buckets: dict[int | None, list[ProcurementNotice]] = {}
    for notice in notices:
        buckets.setdefault(notice.year, []).append(notice)
    result = []
    for year, rows in sorted(buckets.items(), key=lambda item: (item[0] is None, item[0] or 0)):
        result.append({
            "year": year,
            "n_notices": len(rows),
            "n_documents": sum(row.document_status == "available" for row in rows),
            "n_explicit_swedish": sum(row.language_category == "explicit_requirement" for row in rows),
            "n_language_related": sum(row.language_category == "language_related" for row in rows),
            "coverage_statuses": sorted({row.coverage_status for row in rows}),
        })
    return result

