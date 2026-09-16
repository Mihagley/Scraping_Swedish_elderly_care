from __future__ import annotations

import calendar
import re
from datetime import date

from .config import Research
from .models import Chunk, Decision, Document, Location, QuoteCheck


def locate_quote(quote: str, document: Document, chunk: Chunk) -> list[Location]:
    """Offsets are Unicode character offsets in saved text, end-exclusive.

    Whitespace folding is the only non-exact match accepted. No fuzzy matching,
    translation, case folding or ellipsis expansion is permitted.
    """
    if not quote.strip() or len(quote) > 2000:
        return []
    matches = list(re.finditer(re.escape(quote), chunk.text))
    method = "exact"
    if not matches:
        pattern = r"\s+".join(re.escape(word) for word in quote.split())
        matches = list(re.finditer(pattern, chunk.text)) if pattern else []
        method = "whitespace"
    results = []
    for match in matches:
        start, end = chunk.start + match.start(), chunk.start + match.end()
        first = next((p for p in document.pages if p.start <= start < p.end), None)
        last = next((p for p in document.pages if p.start < end <= p.end), None)
        results.append(
            Location(
                match=method,
                start=start,
                end=end,
                line_start=document.text.count("\n", 0, start) + 1,
                line_end=document.text.count("\n", 0, end - 1) + 1,
                page_start=first.page if first else None,
                page_end=last.page if last else None,
                page_char_start=start - first.start if first and first.page else None,
                page_char_end=end - last.start if last and last.page else None,
                matched_text=document.text[start:end],
            )
        )
    return results


def latest_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", value):
        raise ValueError("Date must be YYYY, YYYY-MM, or YYYY-MM-DD")
    parts = [int(p) for p in value.split("-")]
    if len(parts) == 1:
        return date(parts[0], 12, 31)
    if len(parts) == 2:
        return date(parts[0], parts[1], calendar.monthrange(parts[0], parts[1])[1])
    return date(*parts)


def verify_decision(
    decision: Decision, document: Document, chunk: Chunk, research: Research
) -> tuple[list[QuoteCheck], list[str]]:
    issues, checks = [], []
    for i, quote in enumerate(decision.evidence):
        locations = locate_quote(quote.text, document, chunk)
        issue = None if locations else "Quote not found in the supplied source chunk"
        if len(quote.text.strip()) < 12:
            issue = "Quote is too short to provide meaningful evidence (minimum 12 characters)"
        checks.append(
            QuoteCheck(
                quote_index=i,
                quote=quote.text,
                purpose=quote.purpose,
                verified=issue is None,
                locations=locations,
                issue=issue,
            )
        )
        if issue:
            issues.append(f"quote_{i}: {issue}")
    rule = research.labels.get(decision.category)
    if rule is None:
        issues.append("Unknown category")
        return checks, issues
    valid = [q for q in checks if q.verified]
    if rule.requires_evidence and not any(q.purpose == "finding" for q in valid):
        issues.append("Category requires a verified finding quote")
    if rule.requires_before_cutoff:
        if decision.temporal_relation != "before_cutoff":
            issues.append("Category requires evidence of operation before the exclusive cutoff")
        if not any(q.purpose == "timing" for q in valid):
            issues.append("Pre-cutoff claim lacks a verified timing quote")
    if decision.effective_date:
        try:
            bound = latest_date(decision.effective_date)
            if rule.requires_before_cutoff and research.cutoff and bound >= research.cutoff:
                issues.append("Effective-date interval is not wholly before cutoff")
        except ValueError:
            issues.append("Invalid effective_date")
        if not any(q.purpose == "timing" for q in valid):
            issues.append("Effective date requires a verified timing quote")
    if decision.scope and not any(q.purpose == "scope" for q in valid):
        issues.append("Scope requires a verified scope quote")
    seen = set()
    for attribute in decision.attributes:
        if attribute.name not in research.fields or attribute.name in seen:
            issues.append(f"Unknown or duplicate attribute: {attribute.name}")
        seen.add(attribute.name)
        if not attribute.quote_indices or any(
            i < 0 or i >= len(checks) or not checks[i].verified for i in attribute.quote_indices
        ):
            issues.append(f"Attribute lacks verified citations: {attribute.name}")
    return checks, issues
