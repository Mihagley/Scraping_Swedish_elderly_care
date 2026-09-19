"""Conservative possible recruitment spells; original rows are never deleted."""

import hashlib
import re
from collections import defaultdict
from datetime import date


def shingles(text):
    words = re.findall(r"\w+", text)
    return {tuple(words[i : i + 3]) for i in range(max(1, len(words) - 2))}


def assign_spells(records, window_days=45, threshold=0.9):
    if not 0 <= threshold <= 1 or window_days < 0:
        raise ValueError("Invalid deduplication settings")
    rows = sorted(
        (dict(r) for r in records),
        key=lambda r: (r["publication_date"] or "", r["ad_id"], r["record_id"]),
    )
    groups = defaultdict(list)
    seen = set()
    representatives = {}
    for row in rows:
        same_id = row["ad_id"] in seen
        row["duplicate_ad_id"] = same_id
        seen.add(row["ad_id"])
        if same_id:
            row["primary_eligible"] = False
        when = date.fromisoformat(row["publication_date"][:10]) if row["publication_date"] else None
        key = (
            row["employer_orgnr_normalized"] or row["municipality_id"],
            row["municipality_id"],
            row["occupation_code"],
            row["normalized_job_title"],
        )
        tokens = shingles(row["normalized_text"])
        found = None
        if when and key[0] and key[2] and key[3] and tokens:
            for candidate in reversed(groups[key]):
                # Compare to the first ad to prevent an indefinitely chained spell.
                if (when - candidate["date"]).days > window_days:
                    continue
                union = tokens | candidate["tokens"]
                score = len(tokens & candidate["tokens"]) / len(union) if union else 0
                if score >= threshold:
                    found = candidate["spell_id"]
                    break
        spell_id = found or hashlib.sha256(("spell:" + row["record_id"]).encode()).hexdigest()[:24]
        row["recruitment_spell_id"] = spell_id
        if not found:
            groups[key].append({"date": when, "tokens": tokens, "spell_id": spell_id})
        # Only eligible ads define the analytical representative, chosen earliest.
        if row["primary_eligible"]:
            if spell_id not in representatives:
                representatives[spell_id] = {
                    **row,
                    "n_ads_in_spell": 0,
                    "requirement_changed_within_spell": False,
                    "spell_last_date": row["publication_date"],
                    "spell_ad_ids": [],
                }
            representative = representatives[spell_id]
            representative["n_ads_in_spell"] += 1
            representative["requirement_changed_within_spell"] |= (
                row["swedish_requirement"] != representative["swedish_requirement"]
            )
            representative["spell_last_date"] = row["publication_date"]
            representative["spell_ad_ids"].append(row["ad_id"])
    return rows, list(representatives.values())
