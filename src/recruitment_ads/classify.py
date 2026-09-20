"""Pure original-text classifier with audit evidence; no municipal-policy inference."""

import hashlib
import json
import re

from .elderly_care import context
from .employer import normalize_orgnr
from .language_rules import CATEGORIES, FORMAL, VERSION, classify_language
from .schemas import adapt, encoded


def classify_ad(raw, source, master, config, member="", line=0):
    ad = adapt(raw, source, member, line, tuple(config["occupations"]))
    ad["employer_orgnr_normalized"] = normalize_orgnr(ad["employer_orgnr"])
    ad.update(
        master.match(ad["employer_orgnr"], ad["employer_name"], (ad["publication_date"] or "")[:10])
    )
    ec = context(
        ad["original_job_title"],
        ad["description_text"],
        ad["occupation_code"],
        config.get("elderly_context"),
    )
    ad.update({k: encoded(v) if isinstance(v, list) else v for k, v in ec.items()})
    classify_text_fields(ad, config)
    reasons = []
    if ad["employer_match_method"] != "orgnr_exact":
        reasons.append(ad["employer_match_method"])
    if not ad["occupation_eligible"]:
        reasons.append(
            ad["occupation_match_method"]
            if not ad["occupation_code"]
            else "occupation_outside_target"
        )
    if ad["elderly_care_context"] != "yes":
        reasons.append("elderly_context_" + ad["elderly_care_context"])
    if not ad["description_text"].strip():
        reasons.append("missing_text")
    if not ad["publication_date"] or not ad["ad_id"]:
        reasons.append("missing_id_or_date")
    ad["primary_eligible"] = not reasons
    ad["sensitivity_eligible"] = (
        ad["employer_match_method"] != "unresolved"
        and ad["occupation_eligible"]
        and ad["elderly_care_context"] in ("yes", "mixed", "uncertain")
        and bool(ad["description_text"].strip())
        and bool(ad["publication_date"] and ad["ad_id"])
    )
    ad["eligibility_reason"] = "eligible" if not reasons else "|".join(reasons)
    ad["text_hash"] = hashlib.sha256(ad["description_text"].encode()).hexdigest()
    ad["record_id"] = hashlib.sha256(
        json.dumps([ad["ad_id"], source["source_hash"], member, line]).encode()
    ).hexdigest()[:24]
    return ad


def classify_text_fields(ad, config):
    """Apply one versioned language classifier to an already adapted record in place."""
    hits = classify_language(ad["description_text"], config.get("language_patterns"))
    required = [h for h in hits if h["status"] == "required"]
    ad.update({k: any(h["category"] == k for h in required) for k in CATEGORIES})
    ad.update({k + "_hit": any(h["category"] == k for h in hits) for k in CATEGORIES})
    ad["swedish_any_hit"] = bool(hits)
    ad["swedish_requirement"] = ad["our_required_swedish"] = bool(required)
    ad["swedish_preferred"] = ad["our_preferred_swedish"] = any(
        h["status"] == "preferred" for h in hits
    )
    ad["alternative_language_accepted"] = any(h["status"] == "alternative_language" for h in hits)
    ad["formal_threshold"] = any(ad[k] for k in FORMAL)
    ad["cefr_requirement"] = any(ad[k] for k in ("gers_b1", "gers_b2", "other_cefr_level"))
    ad["swedish_course_requirement"] = any(
        ad[k]
        for k in (
            "svenska_1",
            "svenska_som_andrasprak_1",
            "sva_1",
            "svenska_a",
            "svenska_som_andrasprak_a",
        )
    )
    ad["sfi_requirement"] = any(ad[k] for k in CATEGORIES if k.startswith("sfi_"))
    ad["sfi_level"] = "|".join(
        k[-1].upper() for k in ("sfi_a", "sfi_b", "sfi_c", "sfi_d") if ad[k]
    ) or ("unspecified" if ad["sfi_requirement"] else None)
    for key, category in (
        ("strong_qualitative_requirement", "strong_qualitative"),
        ("functional_requirement", "functional_oral_written"),
        ("generic_requirement", "generic_swedish_requirement"),
    ):
        ad[key] = ad[category]
    required_text = " ".join(h["matched_sentence"] for h in required)
    for key, pattern in {
        "oral": r"\btal\b|\btala\b|muntlig",
        "written": r"skrift|skriva|skriftlig",
        "reading": r"\bläsa\b|läsför",
        "understanding": r"förstå|förståelse",
    }.items():
        ad[key + "_requirement"] = bool(re.search(pattern, required_text, re.I))
    ad["classification_status"] = (
        "needs_review" if any(h["status"] == "uncertain" for h in hits) else "deterministic"
    )
    ad["classifier_version"] = VERSION
    ad["dictionary_hash"] = hashlib.sha256(
        encoded({"language": CATEGORIES, "config": config}).encode()
    ).hexdigest()
    ad["language_hits_json"] = encoded(hits)
    for k in ("matched_phrase", "matched_sentence", "context_before", "context_after"):
        ad[k] = encoded(list(dict.fromkeys(h[k] for h in hits)))
    return ad
