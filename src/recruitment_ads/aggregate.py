"""Advertisement and spell denominators; unobserved years and empty cells stay missing."""

import math

import pandas as pd

CATEGORY_MEASURES = {
    "formal_threshold": "formal_threshold",
    "cefr": "cefr_requirement",
    "swedish_course": "swedish_course_requirement",
    "sfi": "sfi_requirement",
    "language_test": "language_test",
    "strong_qualitative": "strong_qualitative_requirement",
    "functional": "functional_requirement",
    "generic": "generic_requirement",
    "gers_b1": "gers_b1",
    "gers_b2": "gers_b2",
}


def measures(rows):
    n = len(rows)
    required = sum(bool(r["swedish_requirement"]) for r in rows)
    preferred = sum(bool(r["swedish_preferred"]) for r in rows)
    known = [
        r
        for r in rows
        if r["number_of_vacancies"] is not None
        and not pd.isna(r["number_of_vacancies"])
        and r["number_of_vacancies"] > 0
    ]
    vacancies = sum(r["number_of_vacancies"] for r in known)
    req_vacancies = sum(r["number_of_vacancies"] for r in known if r["swedish_requirement"])
    missing = n - len(known)
    unknown_required = required - sum(r["swedish_requirement"] for r in known)
    result = {
        "n_ads": n,
        "n_recruitment_spells": len({r["recruitment_spell_id"] for r in rows}),
        "n_vacancies": vacancies if known else None,
        "n_ads_missing_vacancies": missing,
        "n_required_swedish": required,
        "n_preferred_swedish": preferred,
        "share_required_swedish": required / n if n else None,
        "share_required_ads": required / n if n else None,
        "share_preferred_swedish": preferred / n if n else None,
        "share_required_vacancies": req_vacancies / vacancies if vacancies else None,
        "share_required_vacancies_missing_as_one": (req_vacancies + unknown_required)
        / (vacancies + missing)
        if vacancies + missing
        else None,
        "share_no_language_requirement": 1 - required / n if n else None,
        "share_uncertain": sum(r["classification_status"] == "needs_review" for r in rows) / n
        if n
        else None,
        "coverage_flag": "GOOD"
        if n >= 20
        else "MODERATE"
        if n >= 5
        else "SPARSE"
        if n
        else "NO_ADS",
    }
    for label, field in CATEGORY_MEASURES.items():
        result["share_" + label] = sum(bool(r[field]) for r in rows) / n if n else None
    return result


def build_panel(records, municipalities, years, sources, occupations=("5321", "5330")):
    eligible = [r for r in records if r["primary_eligible"] and not r.get("duplicate_ad_id")]
    candidate_groups = {}
    for row in records:
        municipality_id = row.get("municipality_id") or row.get(
            "candidate_employer_municipality_id"
        )
        candidate_groups.setdefault((municipality_id, row["year"], "overall"), []).append(row)
        candidate_groups.setdefault(
            (municipality_id, row["year"], row["occupation_code"]), []
        ).append(row)
    grouped = {}
    for row in eligible:
        grouped.setdefault(
            (row["municipality_id"], row["year"], row["occupation_code"]), []
        ).append(row)
    result = []
    for municipality in municipalities:
        for year in years:
            source = sources.get(year)
            identifier_gap = bool(source and source.get("employer_identifier_gap"))
            year_complete = bool(
                source
                and source["calendar_complete"]
                and not source["is_sample"]
                and not identifier_gap
                and 2016 <= year <= 2025
            )
            per_occ = {}
            for code in (*occupations, "overall"):
                rows = (
                    [
                        r
                        for occ in occupations
                        for r in grouped.get((municipality["municipality_id"], year, occ), [])
                    ]
                    if code == "overall"
                    else grouped.get((municipality["municipality_id"], year, code), [])
                )
                values = measures(rows)
                if not source:
                    values = {k: None for k in values}
                    values["coverage_flag"] = "NOT_PROCESSED"
                elif identifier_gap:
                    values = {k: None for k in values}
                    values["coverage_flag"] = "EMPLOYER_ID_GAP"
                candidates = candidate_groups.get((municipality["municipality_id"], year, code), [])
                candidate_counts = {
                    "n_candidate_ads": len(candidates),
                    "n_context_mixed_candidates": sum(
                        r["elderly_care_context"] == "mixed" for r in candidates
                    ),
                    "n_context_uncertain_candidates": sum(
                        r["elderly_care_context"] == "uncertain" for r in candidates
                    ),
                    "n_unresolved_employer_candidates": sum(
                        r["employer_match_method"] == "unresolved" for r in candidates
                    ),
                }
                row = {
                    "municipality_id": municipality["municipality_id"],
                    "municipality_name": municipality["municipality_name"],
                    "year": year,
                    "occupation_group": code,
                    "year_status": "not_processed"
                    if not source
                    else "employer_identifier_gap"
                    if identifier_gap
                    else "development_sample"
                    if source["is_sample"]
                    else "complete_calendar_year"
                    if year_complete
                    else "year_to_date_incomplete",
                    "complete_year_trend_eligible": year_complete,
                    "validation_status": "manual_validation_pending",
                    **values,
                    **{k: v if source else None for k, v in candidate_counts.items()},
                }
                if code == "overall":
                    row.update({f"share_required_{occ}": per_occ.get(occ) for occ in occupations})
                else:
                    per_occ[code] = values["share_required_swedish"]
                result.append(row)
    return pd.DataFrame(result)


def national_trends(records, sources, weights, geographic_scope):
    if not math.isclose(sum(weights.values()), 1) or any(v < 0 for v in weights.values()):
        raise ValueError("Fixed occupation weights must be nonnegative and sum to one")
    eligible = [r for r in records if r["primary_eligible"] and not r.get("duplicate_ad_id")]
    overall, occupations = [], []
    for year, source in sorted(sources.items()):
        # Partial/calendar sample years never enter default complete-year estimates.
        if (
            not source["calendar_complete"]
            or source["is_sample"]
            or source.get("employer_identifier_gap")
            or not 2016 <= year <= 2025
        ):
            continue
        rows = [r for r in eligible if r["year"] == year]
        rates = {}
        for code in weights:
            values = measures([r for r in rows if r["occupation_code"] == code])
            occupations.append(
                {
                    "year": year,
                    "occupation_group": code,
                    "geographic_scope": geographic_scope,
                    **values,
                }
            )
            rates[code] = values["share_required_swedish"]
        standard = (
            sum(weights[c] * rates[c] for c in weights)
            if all(rates[c] is not None for c in weights)
            else None
        )
        overall.append(
            {
                "year": year,
                "geographic_scope": geographic_scope,
                "all_municipalities_in_scope": geographic_scope == "all_290_municipalities",
                "standardised_share_required": standard,
                "fixed_weights": str(weights),
                **measures(rows),
            }
        )
    return (
        pd.DataFrame(
            overall,
            columns=[
                "year",
                "geographic_scope",
                "all_municipalities_in_scope",
                "standardised_share_required",
                "fixed_weights",
                *measures([]),
            ],
        ),
        pd.DataFrame(
            occupations, columns=["year", "occupation_group", "geographic_scope", *measures([])]
        ),
    )
