from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from .models import Chunk


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(ch for ch in value if not unicodedata.combining(ch))


STRONG_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("language_requirement", r"\bsprak(?:ligt|liga|liga)?krav\w*\b|\bsprakkrav\w*\b", 8),
    ("language_test", r"\bspraktest\w*\b|\bsprakbedom\w*\b|\bsprakprov\w*\b", 7),
    ("language_competence", r"\bsprakkompetens\w*\b|\bsprakkunskap\w*\b|\bsprakniva\w*\b", 6),
    ("swedish_as_second_language", r"\bsvenska som andrasprak\b|\bsva\s*1\b", 8),
    ("swedish_1", r"\bsvenska\s*1\b", 8),
    ("sfi", r"\bsfi\b(?:\s*(?:kurs|niva)?\s*[a-d])?", 7),
    ("cefr", r"\b(?:gers|cefr)\b.{0,100}\b[abc][12]\b|\b[abc][12]\b.{0,100}\b(?:gers|cefr)\b", 8),
)

CARE_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("elderly_care", r"\baldreomsorg\w*\b|\baldreboende\w*\b", 5),
    ("home_care", r"\bhemtjanst\w*\b", 5),
    ("special_housing", r"\bsarskilt\s+boende\b", 4),
    ("care_staff", r"\bomsorgspersonal\w*\b|\bunderskotersk\w*\b|\bvardbitrade\w*\b", 4),
    ("care_phrase", r"\bvard\s+och\s+omsorg\b", 3),
)

FORMAL_PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("formal_requirement", r"\bkrav\w*\b|\bmaste\b|\bskall\b|\bska\b", 3),
    ("decision", r"\bbeslut\w*\b|\bantog\w*\b|\bantagits\b", 3),
    ("implementation", r"\binfor\w*\b|\bimplement\w*\b|\bgaller\b|\btillamp\w*\b", 3),
    ("employment", r"\brekryter\w*\b|\banstall\w*\b|\bnyanstall\w*\b", 3),
    ("procurement", r"\bupphandl\w*\b|\bavtal\w*\b|\bforfragningsunderlag\w*\b", 3),
    ("governing_document", r"\briktlinj\w*\b|\bpolicy\w*\b|\bprotokoll\w*\b", 2),
)

GENERAL_LANGUAGE = re.compile(r"\bsvensk\w*\b|\bsprak\w*\b")
YEAR_PATTERN = re.compile(r"\b20(?:1\d|2[0-6])\b")


def _matches(text: str, patterns: tuple[tuple[str, str, int], ...]) -> list[tuple[str, int]]:
    return [(name, weight) for name, pattern, weight in patterns if re.search(pattern, text)]


def score_chunk(chunk: Chunk, document: dict[str, Any]) -> dict[str, Any]:
    """Score a chunk using only local text/title/URL; no model or network calls."""
    text = _fold(chunk.text)
    context = _fold(f"{document.get('title', '')} {document.get('url', '')}")
    combined = f"{context}\n{text}"
    strong = _matches(combined, STRONG_PATTERNS)
    care = _matches(combined, CARE_PATTERNS)
    formal = _matches(combined, FORMAL_PATTERNS)
    general_language = bool(GENERAL_LANGUAGE.search(combined))
    has_year = bool(YEAR_PATTERN.search(text))

    reasons = [name for name, _ in strong + care + formal]
    score = sum(weight for _, weight in strong + care + formal) + (1 if has_year else 0)
    if has_year:
        reasons.append("dated_text")

    # Preserve recall for explicit level/test/requirement language even when the elderly-care
    # context is in the title or adjacent chunk. Generic Swedish-language mentions require
    # both elderly-care context and a formal-policy signal.
    selected = bool(strong and (care or formal)) or bool(general_language and care and formal)
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "start": chunk.start,
        "end": chunk.end,
        "score": score,
        "selected_direct": selected,
        "reasons": sorted(set(reasons)),
    }


def select_chunks(
    chunks: list[Chunk],
    documents: dict[str, dict[str, Any]],
    *,
    max_selected: int,
    context_neighbors: int = 1,
) -> tuple[list[Chunk], list[dict[str, Any]], bool]:
    """Select high-recall policy chunks and adjacent context under a deterministic budget."""
    if max_selected < 1:
        raise ValueError("max_selected must be at least 1")
    rows = [score_chunk(chunk, documents[chunk.document_id]) for chunk in chunks]
    by_id = {chunk.id: chunk for chunk in chunks}
    row_by_id = {row["chunk_id"]: row for row in rows}
    by_document: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in chunks:
        by_document[chunk.document_id].append(chunk)
    for values in by_document.values():
        values.sort(key=lambda item: (item.start, item.end, item.id))

    selected_ids = {row["chunk_id"] for row in rows if row["selected_direct"]}
    for document_id, values in by_document.items():
        positions = {chunk.id: index for index, chunk in enumerate(values)}
        direct_ids = [chunk.id for chunk in values if chunk.id in selected_ids]
        for direct_id in direct_ids:
            index = positions[direct_id]
            for neighbor_index in range(
                max(0, index - context_neighbors), min(len(values), index + context_neighbors + 1)
            ):
                neighbor = values[neighbor_index]
                if neighbor.id not in selected_ids:
                    selected_ids.add(neighbor.id)
                    row = row_by_id[neighbor.id]
                    row["reasons"] = sorted(set([*row["reasons"], "adjacent_context"]))
                    row["score"] = max(row["score"], row_by_id[direct_id]["score"] - 1)

    ranked = sorted(
        (row for row in rows if row["chunk_id"] in selected_ids),
        key=lambda row: (-row["score"], row["document_id"], row["start"], row["chunk_id"]),
    )
    limited = len(ranked) > max_selected
    kept = ranked[:max_selected]
    kept_ids = {row["chunk_id"] for row in kept}
    for row in rows:
        row["selected"] = row["chunk_id"] in kept_ids
        if row["chunk_id"] in selected_ids and row["chunk_id"] not in kept_ids:
            row["reasons"] = sorted(set([*row["reasons"], "triage_budget_excluded"]))

    selected_chunks = [by_id[row["chunk_id"]] for row in kept]
    return selected_chunks, rows, limited
