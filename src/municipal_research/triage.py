from __future__ import annotations

import re

from .models import Chunk

# Deliberately high-recall local filter. It reduces API use without treating an
# omitted chunk as evidence of absence. Ambiguous governance/care material remains
# eligible for classification.
PATTERNS = {
    "svenska_1": r"\bsvenska\s*(?:nivå\s*)?1\b|\bsvenska\s+a\b",
    "svenska_som_andrasprak_1": r"\bsvenska\s+som\s+andraspråk\s*(?:nivå\s*)?1\b|\bsva\s*1\b",
    "sfi": r"\bsfi\b|svenska\s+för\s+invandrare",
    "gers_b1": r"\b(?:gers|cefr)\s*[-:]?\s*b1\b|\bb1[- ]nivå\b",
    "gers_b2": r"\b(?:gers|cefr)\s*[-:]?\s*b2\b|\bb2[- ]nivå\b",
    "qualitative": r"språkkrav|språktest|språkförmåga|språkkunskap|kunskaper?\s+i\s+svenska|behärska\s+svenska|god\s+svenska|tillräcklig\w*\s+svenska|krav\s+(?:på|om)\s+svenska|svenska\s+på\s+nivå\s+[a-c][12]",
    "elderly_care": r"äldreomsorg|hemtjänst|särskilt\s+boende|äldreboende|vårdbiträde|underskötersk|vård\s+och\s+omsorg",
    "governance": r"protokoll|tjänsteskrivelse|sammanträde|nämnd|kommunstyrelse|kommunfullmäktige|beslut|yrkande|motion|budget|uppdrag",
    "employment": r"anställ|rekryter|personal|medarbet|nyanställ|kompetenskrav|utförare|entreprenör",
    "history": r"infördes|införande|implementer|började|sedan\s+20\d\d|från\s+och\s+med|gäller\s+sedan|trädde\s+i\s+kraft",
}


def triage_chunk(chunk: Chunk) -> dict:
    text = chunk.text.casefold()
    hits = {
        name: [m.group(0) for m in re.finditer(pattern, text, flags=re.IGNORECASE)][:12]
        for name, pattern in PATTERNS.items()
    }
    hits = {name: values for name, values in hits.items() if values}
    language = any(
        key in hits
        for key in [
            "svenska_1",
            "svenska_som_andrasprak_1",
            "sfi",
            "gers_b1",
            "gers_b2",
            "qualitative",
        ]
    )
    care = "elderly_care" in hits
    context = any(key in hits for key in ["governance", "employment", "history"])
    if language and care:
        status = "relevant"
        reason = "language and elderly-care terms co-occur"
    elif (language or care) and context:
        status = "uncertain"
        reason = "partial topic match with governance/employment/historical context"
    else:
        status = "irrelevant"
        reason = "no local high-recall topic/context combination"
    return {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "status": status,
        "reason": reason,
        "hits": hits,
    }
