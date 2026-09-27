#!/usr/bin/env python3
"""Compact an extracted municipal research shard to policy-relevant text passages."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

PATTERNS = {
    "strong": [
        r"språkkrav", r"sprakkrav", r"språktest", r"spraktest",
        r"språkbedöm", r"sprakbedom", r"krav\w*[^\n]{0,80}svensk",
        r"svensk\w*[^\n]{0,80}krav",
        r"svenska\s*(?:som\s+andraspråk\s*)?1\b", r"sva\s*1\b",
        r"gers\s*[ab][12]\b", r"cefr\s*[ab][12]\b",
        r"\bb2\b[^\n]{0,50}(?:gers|cefr|svensk)", r"sfi\s*[a-d]\b",
    ],
    "development": [
        r"språkombud", r"sprakombud", r"yrkessvensk", r"språkutveckl",
        r"sprakutveckl", r"språkstöd", r"sprakstod", r"språkutbild",
        r"sprakutbild", r"språkcoach", r"sprakcoach",
    ],
    "care": [
        r"äldreomsorg", r"aldreomsorg", r"äldreboende", r"aldreboende",
        r"vård-?\s*och\s*omsorg", r"vard-?\s*och\s*omsorg",
        r"hemtjänst", r"hemtjanst", r"omsorgspersonal", r"vårdboende",
        r"vardboende",
    ],
    "employment": [
        r"nyanställ", r"nyanstall", r"rekryter", r"anställ", r"anstall",
        r"medarbet", r"personal", r"vikarie", r"underskötersk",
        r"underskotersk",
    ],
    "governance": [
        r"beslut", r"protokoll", r"motion", r"riktlinj", r"policy",
        r"kravspec", r"förfrågningsunderlag", r"forfragningsunderlag",
        r"upphandling", r"tjänsteutlåtande", r"tjansteutlatande",
    ],
    "history": [
        r"inför", r"infor", r"infördes", r"infordes", r"sedan\s+20\d\d",
        r"från\s+(?:den\s+)?\d", r"gäller\s+från", r"trädde\s+i\s+kraft",
        r"har\s+sedan", r"tidigare", r"ändra", r"förändr", r"utöka",
        r"utoka", r"ersätt", r"upphör", r"upphor",
    ],
}
RX = {k: [re.compile(p, re.I) for p in ps] for k, ps in PATTERNS.items()}


def hit_count(text: str, group: str) -> int:
    return sum(bool(rx.search(text)) for rx in RX[group])


def score(text: str) -> tuple[int, dict[str, int]]:
    h = {k: hit_count(text, k) for k in RX}
    value = (
        14 * h["strong"] + 8 * h["development"] + 5 * h["care"]
        + 3 * h["employment"] + 2 * h["governance"] + 2 * h["history"]
    )
    if (h["strong"] or h["development"]) and h["care"]:
        value += 12
    if h["strong"] and h["employment"]:
        value += 7
    if h["strong"] and h["governance"]:
        value += 5
    if h["development"] and h["employment"]:
        value += 3
    return value, h


def excerpt(text: str, limit: int = 5000) -> str:
    positions: list[int] = []
    for group in ("strong", "development", "care"):
        for rx in RX[group]:
            m = rx.search(text)
            if m:
                positions.append(m.start())
    if not positions:
        return text[:limit]
    start = max(0, min(positions) - 1500)
    end = min(len(text), start + limit)
    return text[max(0, end - limit):end]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    result_files = list(root.rglob("results.json"))
    summaries: dict[str, dict] = {}
    docs: dict[str, dict] = {}
    doc_to_mid: dict[str, str] = {}

    for path in result_files:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not obj.get("summary"):
            continue
        sm = obj["summary"][0]
        mid = sm["municipality_id"]
        summaries[mid] = sm
        for d in obj.get("documents", []):
            docs[d["id"]] = d
            doc_to_mid[d["id"]] = mid

    by_mid: dict[str, list[dict]] = defaultdict(list)
    for path in root.rglob("chunks/*.json"):
        try:
            c = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        did = c.get("document_id")
        mid = doc_to_mid.get(did)
        if not mid:
            continue
        d = docs.get(did, {})
        text = c.get("text", "") or ""
        combined = (d.get("title", "") + "\n" + unquote(d.get("url", "")) + "\n" + text)
        sc, hits = score(combined)
        if sc <= 0:
            continue
        by_mid[mid].append({
            "score": sc,
            "hits": hits,
            "chunk_id": c.get("id"),
            "document_id": did,
            "chunk_start": c.get("start"),
            "chunk_end": c.get("end"),
            "title": d.get("title"),
            "url": d.get("url"),
            "text_sha256": d.get("text_sha256"),
            "media_type": d.get("media_type"),
            "warnings": d.get("warnings", []),
            "excerpt": excerpt(text),
        })

    with Path(args.out).open("w", encoding="utf-8") as fh:
        for mid, sm in sorted(summaries.items()):
            cands = sorted(
                by_mid.get(mid, []),
                key=lambda x: (x["score"], len(x["excerpt"])),
                reverse=True,
            )
            selected: list[dict] = []
            seen: set[tuple[str, str]] = set()
            per_doc: dict[str, int] = defaultdict(int)
            for c in cands:
                signature = (
                    c["document_id"],
                    re.sub(r"\s+", " ", c["excerpt"][:500]),
                )
                if signature in seen or per_doc[c["document_id"]] >= 4:
                    continue
                selected.append(c)
                seen.add(signature)
                per_doc[c["document_id"]] += 1
                if len(selected) >= 30:
                    break
            fh.write(json.dumps({
                "municipality_id": mid,
                "municipality": sm.get("municipality"),
                "coverage": sm.get("coverage"),
                "gaps": sm.get("gaps", []),
                "documents_collected": sm.get("documents"),
                "candidate_count_all": len(cands),
                "candidate_chunks": selected,
            }, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
