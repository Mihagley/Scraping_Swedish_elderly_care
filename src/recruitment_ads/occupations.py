"""Interpret SSYK from occupation_group, never from occupation's legacy job-title code."""

import re


def objects(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def occupation(raw, targets=("5321", "5330")):
    groups = objects(raw.get("occupation_group"))
    codes = sorted(
        {str(g.get("ssyk_code") or g.get("legacy_ams_taxonomy_id") or "") for g in groups} - {""}
    )
    target = sorted(set(codes) & set(targets))
    original = objects(raw.get("occupation"))
    job_title = raw.get("headline") or ""
    result = {
        "occupation_code": codes[0] if len(codes) == 1 else None,
        "occupation_codes_json": __import__("json").dumps(codes),
        "occupation_label": "; ".join(g.get("label") or "" for g in groups),
        "original_occupation_labels": "; ".join(g.get("label") or "" for g in original),
        "occupation_match_method": "structured_ssyk",
        "occupation_candidate": bool(target),
        "occupation_eligible": len(codes) == 1 and bool(target),
    }
    if len(codes) > 1 and target:
        result["occupation_match_method"] = "multiple_groups_review"
    if not codes:
        result["occupation_candidate"] = bool(
            re.search(
                r"\b(underskötersk\w*|vårdbiträd\w*)",
                job_title + " " + result["original_occupation_labels"],
                re.I,
            )
        )
        result["occupation_match_method"] = "title_candidate_only"
    elif set(target) & {"5321", "5330"} and re.search(
        r"\b(?:sjukskötersk\w*|arbetsterapeut\w*|fysioterapeut\w*|enhetschef\w*|verksamhetschef\w*|socionom\w*|kock\w*|städare|personlig assistent)\b",
        job_title,
        re.I,
    ):
        result["occupation_eligible"] = False
        result["occupation_match_method"] = "structured_title_conflict_review"
    return result
