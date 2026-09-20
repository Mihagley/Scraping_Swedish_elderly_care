"""Explicit older-record adapter; never promotes reconstructed cases to the primary cohort."""

import hashlib
import json
import re
from collections import defaultdict

import pyarrow as pa

from .classify import classify_ad
from .elderly_care import context
from .occupations import objects, occupation
from .schemas import SCHEMA, encoded

VERSION = "historical-adapter-1.1.0"
EXTRA_STRINGS = "source_ad_id ad_identity_method legacy_occupation_ids_json occupation_mapping_status occupation_mapping_source occupation_mapping_hash historical_occupation_group historical_cohort historical_context historical_context_evidence historical_exclusion_evidence historical_recruitment_spell_id municipality_id_basis raw_record_hash original_legacy_attributes_json".split()
HISTORICAL_SCHEMA = pa.schema(
    list(SCHEMA)
    + [(key, pa.string()) for key in EXTRA_STRINGS]
    + [("historical_eligible", pa.bool_())]
)
TARGETS = {"5321", "5330"}


class LegacyOccupations:
    def __init__(self, records, source_url, source_hash):
        self.by_code = defaultdict(list)
        self.source_url, self.source_hash = source_url, source_hash
        for row in records:
            code = row.get("taxonomy/deprecated-legacy-id")
            if code:
                self.by_code[str(code)].append(row)

    def resolve(self, raw):
        existing = occupation(raw)
        legacy_codes = sorted(
            {
                str(r["legacy_ams_taxonomy_id"])
                for r in objects(raw.get("occupation"))
                if r.get("legacy_ams_taxonomy_id") is not None
            }
        )
        details = {
            "legacy_occupation_ids_json": encoded(legacy_codes),
            "occupation_mapping_source": self.source_url,
            "occupation_mapping_hash": self.source_hash,
        }
        group_codes = json.loads(existing["occupation_codes_json"])
        if group_codes:
            return raw, {**details, "occupation_mapping_status": "source_structured_ssyk"}
        concepts = [r for code in legacy_codes for r in self.by_code.get(code, [])]
        mapped = {
            str(r["taxonomy/ssyk-code-2012"]) for r in concepts if r.get("taxonomy/ssyk-code-2012")
        }
        all_resolved = bool(legacy_codes) and all(self.by_code.get(code) for code in legacy_codes)
        if len(mapped) != 1 or not all_resolved:
            return raw, {
                **details,
                "occupation_mapping_status": "ambiguous_legacy_mapping"
                if len(mapped) > 1
                else "unmapped_legacy_code",
            }
        code = next(iter(mapped))
        adapted = {
            **raw,
            "occupation_group": [
                {
                    "legacy_ams_taxonomy_id": code,
                    "label": f"SSYK 2012 {code} (official legacy crosswalk)",
                }
            ],
        }
        return adapted, {**details, "occupation_mapping_status": "official_legacy_crosswalk"}


def title_group(title):
    nurse = bool(re.search(r"\bunderskötersk\w*", title, re.I))
    aide = bool(re.search(r"\bvårdbiträd\w*", title, re.I))
    return (
        "title_mixed_frontline"
        if nurse and aide
        else "title_underskoterska"
        if nurse
        else "title_vardbitrade"
        if aide
        else None
    )


def role_candidate(raw, crosswalk):
    adapted, _ = crosswalk.resolve(raw)
    return occupation(adapted)["occupation_candidate"] or bool(
        title_group(raw.get("headline") or "")
    )


def attach_historical_cohort(ad, mapping):
    """Require explicit job context consistently in all years, even mapped SSYK 5321."""
    ec = context(ad["original_job_title"], ad["description_text"], None)
    ad.update(mapping)
    ad.update(
        {
            "historical_context": ec["elderly_care_context"],
            "historical_context_evidence": encoded(ec["elderly_context_evidence"]),
            "historical_exclusion_evidence": encoded(ec["exclusion_context_evidence"]),
            "municipality_id_basis": "current_master_legal_entity_key_historical_continuity_unverified",
        }
    )
    group = (
        ad["occupation_code"]
        if ad["occupation_code"] in TARGETS
        else title_group(ad["original_job_title"])
    )
    unknown = (
        ad["occupation_mapping_status"] == "unmapped_legacy_code" and not ad["occupation_code"]
    )
    title_conflict = re.search(
        r"\b(?:sjukskötersk\w*|arbetsterapeut\w*|fysioterapeut\w*|enhetschef\w*|verksamhetschef\w*|socionom\w*|kock\w*|städare|personlig assistent)\b",
        ad["original_job_title"],
        re.I,
    )
    mapped_ok = ad["occupation_eligible"] and ad["occupation_code"] in TARGETS
    title_ok = unknown and bool(group) and not title_conflict
    allowed = (
        ad["employer_match_method"] in {"orgnr_exact", "employer_name_validated"}
        and ec["elderly_care_context"] == "yes"
        and bool(ad["description_text"].strip() and ad["publication_date"] and ad["ad_id"])
        and not ad.get("duplicate_ad_id")
    )
    ad["historical_cohort"] = (
        "mapped_occupation_sensitivity"
        if allowed and mapped_ok
        else "unmapped_title_context_exploratory"
        if allowed and title_ok
        else "review_only"
    )
    ad["historical_occupation_group"] = group
    ad["historical_eligible"] = ad["historical_cohort"] != "review_only"
    return ad


def classify_historical(raw, source, master, config, crosswalk, member, line):
    adapted, mapping = crosswalk.resolve(raw)
    source_id = raw.get("id") or raw.get("original_id")
    # A source locator identifies an observed record, not a recovered official ad ID.
    locator = hashlib.sha256(encoded([source["source_hash"], member, line]).encode()).hexdigest()
    adapted = {**adapted, "id": str(source_id) if source_id else "source-record:" + locator}
    ad = classify_ad(adapted, source, master, config, member, line)
    ad.update(
        {
            "source_ad_id": str(source_id) if source_id else None,
            "ad_identity_method": "official_ad_id" if source_id else "source_record_locator",
            "original_ad_id": str(raw.get("original_id") or ""),
            "original_occupation_json": encoded(raw.get("occupation")),
            "original_occupation_group_json": encoded(raw.get("occupation_group")),
            "original_legacy_attributes_json": encoded(raw.get("other_old_legacy_attributes")),
            "raw_record_hash": hashlib.sha256(encoded(raw).encode()).hexdigest(),
            "primary_eligible": False,
        }
    )
    ad["eligibility_reason"] = "historical_extension_separate|" + ad["eligibility_reason"]
    return attach_historical_cohort(ad, mapping)
