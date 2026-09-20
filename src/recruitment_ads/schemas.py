"""Stable ad schema, including originals, evidence, missingness and source provenance."""

import json
import re
import unicodedata
from datetime import datetime

import pyarrow as pa

from .language_rules import CATEGORIES
from .occupations import objects, occupation

STRINGS = "record_id ad_id original_ad_id publication_date employer_name employer_orgnr employer_orgnr_normalized employer_match_method employer_validity_basis municipality_id municipality_name workplace_municipality workplace_municipality_code occupation_code occupation_label occupation_codes_json original_occupation_labels occupation_match_method original_job_title normalized_job_title employment_type employment_duration description_text normalized_text source_file source_member source_url source_hash retrieved_at data_version elderly_care_context elderly_context_basis elderly_context_evidence exclusion_context_evidence af_must_have_languages_json af_nice_to_have_languages_json original_occupation_json original_occupation_group_json language_hits_json sfi_level classification_status classifier_version dictionary_hash matched_phrase matched_sentence context_before context_after manual_validation_status recruitment_spell_id text_hash eligibility_reason".split()
BOOLS = (
    "occupation_candidate occupation_eligible primary_eligible sensitivity_eligible swedish_any_hit swedish_requirement swedish_preferred our_required_swedish our_preferred_swedish formal_threshold cefr_requirement swedish_course_requirement sfi_requirement strong_qualitative_requirement functional_requirement generic_requirement oral_requirement written_requirement reading_requirement understanding_requirement alternative_language_accepted af_must_have_swedish af_nice_to_have_swedish is_sample calendar_complete duplicate_ad_id".split()
    + list(CATEGORIES)
    + [c + "_hit" for c in CATEGORIES]
)
SCHEMA = pa.schema(
    [(k, pa.string()) for k in STRINGS + ["candidate_employer_municipality_id"]]
    + [(k, pa.bool_()) for k in dict.fromkeys(BOOLS)]
    + [(k, pa.int64()) for k in ("year", "month", "number_of_vacancies", "source_line")]
)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def labels(value):
    return "; ".join(v.get("label") or "" for v in objects(value))


def af_swedish(raw, field):
    container = raw.get(field)
    if (
        not isinstance(container, dict)
        or "languages" not in container
        or container["languages"] is None
    ):
        return None
    return any(
        re.search(r"\bsvenska\b|^swedish$", str(v.get("label") or ""), re.I)
        for v in objects(container["languages"])
    )


def adapt(raw, source, member="", line=0, targets=("5321", "5330")):
    if "employer" not in raw or "publication_date" not in raw:
        raise ValueError("Unsupported historical schema; use a separately validated adapter")
    date_text = raw.get("publication_date")
    date = datetime.fromisoformat(date_text.replace("Z", "+00:00")) if date_text else None
    employer = raw.get("employer") or {}
    address = raw.get("workplace_address") or {}
    desc = raw.get("description") or {}
    text = desc.get("text") if isinstance(desc, dict) else desc
    text = text or ""
    vacancies = raw.get("number_of_vacancies")
    # Keep only meaningful positive counts; missing/zero/negative counts remain missing.
    count = (
        int(vacancies)
        if isinstance(vacancies, (int, float))
        and not isinstance(vacancies, bool)
        and vacancies > 0
        and vacancies == int(vacancies)
        else None
    )
    result = {k: None for k in SCHEMA.names}
    result.update(
        {
            "ad_id": str(raw.get("id") or raw.get("original_id") or ""),
            "original_ad_id": str(raw.get("original_id") or ""),
            "publication_date": date_text,
            "year": date.year if date else None,
            "month": date.month if date else None,
            "employer_name": employer.get("name"),
            "employer_orgnr": employer.get("organization_number"),
            "workplace_municipality": address.get("municipality"),
            "workplace_municipality_code": address.get("municipality_code"),
            "original_job_title": raw.get("headline") or "",
            "normalized_job_title": normalize(raw.get("headline") or ""),
            "description_text": text,
            "normalized_text": normalize(text),
            "number_of_vacancies": count,
            "employment_type": labels(raw.get("employment_type")),
            "employment_duration": labels(raw.get("duration")),
            "af_must_have_swedish": af_swedish(raw, "must_have"),
            "af_nice_to_have_swedish": af_swedish(raw, "nice_to_have"),
            "af_must_have_languages_json": encoded((raw.get("must_have") or {}).get("languages")),
            "af_nice_to_have_languages_json": encoded(
                (raw.get("nice_to_have") or {}).get("languages")
            ),
            "original_occupation_json": encoded(raw.get("occupation")),
            "original_occupation_group_json": encoded(raw.get("occupation_group")),
            "source_member": member,
            "source_line": line,
            "manual_validation_status": "not_reviewed",
            "duplicate_ad_id": False,
        }
    )
    result.update(
        {
            k: source.get(k)
            for k in (
                "source_file",
                "source_url",
                "source_hash",
                "retrieved_at",
                "data_version",
                "is_sample",
                "calendar_complete",
            )
        }
    )
    result.update(occupation(raw, targets))
    return result
