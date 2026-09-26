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


# Patterns run on casefolded, whitespace-collapsed text.
_EXPLICIT = (
    r"\bkrav(?:et)?\s+(?:på|om)\s+(?:goda\s+)?(?:kunskaper\s+i\s+)?(?:det\s+)?svenska\b",
    r"\bspråkkrav\b",
    r"\bgod(?:a)?\s+(?:kunskaper\s+i\s+)?svenska\b",
    r"\bsvenska\s+i\s+(?:tal|skrift)(?:\s+och\s+(?:tal|skrift))?\b",
    r"\b(?:tala|läsa|skriva|förstå)(?:\s*,\s*|\s+och\s+|\s+)(?:(?:tala|läsa|skriva|förstå)(?:\s*,\s*|\s+och\s+|\s+))*svenska\b",
    r"\bbehärska(?:r)?\s+(?:det\s+)?svenska\b",
    r"\bkommunicera\s+på\s+svenska\b",
    r"\b(?:kunskaper|kunskap|färdigheter|förmåga)\s+i\s+(?:det\s+)?svenska(?:\s+språket)?\b",
    r"\byrkessvenska\b",
    r"\b(?:svenska|sva)\s*(?:1|2|3)\b",
    r"\bsvenska\s+som\s+andraspråk\b",
    r"\b(?:cefr|gers|gemensam\s+europeisk\s+referensram)\b.{0,60}?\b(?:b1|b2|c1|c2)\b",
    r"\b(?:b1|b2|c1|c2)\b.{0,40}?\b(?:cefr|gers|gemensam\s+europeisk\s+referensram)\b",
)
_RELATED = (
    r"\bspråkombud\b",
    r"\btolk(?:ning|ar|en)?\b",
    r"\bspråkutbildning\b",
    r"\bspråkutvecklande\b",
    r"\bsvenska\s+språket\b",
    r"\bsfi\b",
)
# A match whose surrounding sentence concerns the service user's language
# (right to an interpreter, minority languages, mother tongue) is not a
# staff requirement. It is downgraded to "language_related".
_USER_CONTEXT = re.compile(
    r"\b(?:omsorgstagare\w*|brukare\w*|kund(?:en|er|erna)?|den enskilde|vårdtagare\w*|"
    r"tolk\w*|minoritetsspråk\w*|modersmål\w*|finska|meänkieli|samiska|jiddisch|romani|"
    r"inte talar|inte behärskar|annat språk än svenska)\b"
)
_STAFF_CONTEXT = re.compile(r"\b(?:personal\w*|medarbetare\w*|anställd\w*|utförare\w*|den som utför)\b")


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start), text.rfind("•", 0, start))
    right_candidates = [i for i in (text.find(". ", end), text.find("; ", end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return (left + 1 if left != -1 else 0), right


def _snippet(text: str, start: int, end: int) -> str:
    return text[max(0, start - 100): min(len(text), end + 140)].strip()


def classify_language_requirement(text: str | None) -> LanguageFinding:
    """Classify only what is stated in the supplied notice/document text.

    Matching and snippet extraction use the same normalized string, so the
    evidence always contains the matched phrase.
    """
    if not text or not text.strip():
        return LanguageFinding("missing_document")
    readable = re.sub(r"\s+", " ", text).strip()
    normalized = readable.casefold()  # same length as ``readable`` for Swedish text
    if len(normalized) != len(readable):
        readable = normalized
    downgraded: LanguageFinding | None = None
    for pattern in _EXPLICIT:
        for match in re.finditer(pattern, normalized):
            s_start, s_end = _sentence_bounds(normalized, match.start(), match.end())
            sentence = normalized[s_start:s_end]
            if _USER_CONTEXT.search(sentence) and not _STAFF_CONTEXT.search(sentence):
                if downgraded is None:
                    downgraded = LanguageFinding("language_related", _snippet(readable, match.start(), match.end()), "medium")
                continue
            return LanguageFinding("explicit_requirement", _snippet(readable, match.start(), match.end()), "high")
    if downgraded is not None:
        return downgraded
    for pattern in _RELATED:
        match = re.search(pattern, normalized)
        if match:
            return LanguageFinding("language_related", _snippet(readable, match.start(), match.end()), "medium")
    return LanguageFinding("no_evidence", "", "high")


# Screening for elderly-care procurements. CPV 85311100 = välfärdstjänster för äldre.
_ELDERLY_CPV = ("85311100",)
_ELDERLY_TERMS = re.compile(
    r"\b(?:äldreomsorg\w*|hemtjänst\w*|särskil(?:t|da)\s+boende\w*|äldreboende\w*|"
    r"vård-\s+och\s+omsorgsboende\w*|omsorgsboende\w*|korttidsboende\w*|dagverksamhet\w*|"
    r"trygghetslarm\w*|serviceboende\w*|demensboende\w*|äldre\s+personer)\b"
)


def is_elderly_care(row: dict[str, Any]) -> bool:
    """True if a raw export row looks like an elderly-care procurement."""
    cpv = _first(row, "cpv", "cpv_code", "cpv_codes", "main_cpv", "classification-cpv")
    if any(code in re.sub(r"\D", "", cpv) for code in _ELDERLY_CPV):
        return True
    title = _first(row, "title", "notice_title", "notice-title").casefold()
    if _ELDERLY_TERMS.search(title):
        return True
    description = _first(row, "description", "short_description").casefold()
    return bool(_ELDERLY_TERMS.search(description))


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

