"""County aggregation of frozen recruitment classifications, with fixed geography."""

import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from .historical_pipeline import COHORT_GROUPS, historical_measures, source_period

VERSION = "regional-aggregation-1.0.0"
GEOGRAPHY = "SCB_2026_current_county_fixed_all_years"
SCB_URL = "https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/"


def county_mapping(scb_html, master):
    """Read explicit county headings and municipal rows from the official snapshot."""
    soup = BeautifulSoup(Path(scb_html).read_text(encoding="utf-8-sig"), "html.parser")
    current, counties, municipalities = None, {}, {}
    for line in soup.get_text("\n", strip=True).splitlines():
        county = re.fullmatch(r"(\d{2})\s+(.+ län)", line.strip())
        municipality = re.fullmatch(r"(\d{4})\s+([A-Za-zÅÄÖåäöÉé -]+)", line.strip())
        if county:
            current = county.groups()
            counties[current[0]] = current[1]
        elif municipality:
            code, name = municipality.groups()
            if current is None or not code.startswith(current[0]):
                raise ValueError(f"SCB municipality/county heading mismatch: {code}")
            row = (name, *current)
            if code in municipalities and municipalities[code] != row:
                raise ValueError(f"Conflicting county mapping: {code}")
            municipalities[code] = row
    if len(counties) != 21 or len(municipalities) != 290:
        raise ValueError("SCB county mapping must contain 21 counties and 290 municipalities")
    if {r["municipality_id"] for r in master} != set(municipalities):
        raise ValueError("Municipality master and SCB county snapshot do not have identical keys")
    rows = []
    for row in master:
        name, region_id, region_name = municipalities[row["municipality_id"]]
        if row["municipality_name"] != name:
            raise ValueError(f"Municipality name mismatch: {row['municipality_id']}")
        rows.append(
            {
                "municipality_id": row["municipality_id"],
                "municipality_name": name,
                "region_id": region_id,
                "region_name": region_name,
                "geography_basis": GEOGRAPHY,
                "source_url": SCB_URL,
            }
        )
    return rows


def regional_panel(records, mapping, years, processed_years, weights=None):
    """Pool eligible records, never municipality percentages; preserve both cohorts."""
    weights = weights or {"5321": 0.5, "5330": 0.5}
    if (
        set(weights) != {"5321", "5330"}
        or any(w < 0 for w in weights.values())
        or abs(sum(weights.values()) - 1) > 1e-9
    ):
        raise ValueError("Fixed occupation weights must cover 5321/5330 and sum to one")
    by_municipality = {r["municipality_id"]: r for r in mapping}
    if len(by_municipality) != len(mapping):
        raise ValueError("Duplicate municipality mapping")
    counties = {r["region_id"]: r["region_name"] for r in mapping}
    county_sizes = Counter(r["region_id"] for r in mapping)
    grouped = defaultdict(list)
    for row in records:
        if not row["historical_eligible"] or row.get("duplicate_ad_id"):
            continue
        if row["municipality_id"] not in by_municipality:
            raise ValueError(f"Eligible employer has no county mapping: {row['municipality_id']}")
        region = by_municipality[row["municipality_id"]]["region_id"]
        cohort, occupation = row["historical_cohort"], row["historical_occupation_group"]
        if cohort not in COHORT_GROUPS or occupation not in COHORT_GROUPS[cohort]:
            raise ValueError("Eligible row has an unknown cohort/occupation")
        if row["year"] not in processed_years:
            raise ValueError("Eligible row belongs to a year not recorded as processed")
        grouped[region, row["year"], cohort, occupation].append(row)
    result = []
    for region_id, name in sorted(counties.items()):
        for year in years:
            for cohort, groups in COHORT_GROUPS.items():
                occupation_rows = {g: grouped[region_id, year, cohort, g] for g in groups}
                rates = {
                    g: historical_measures(rows)["share_required_ads"]
                    for g, rows in occupation_rows.items()
                }
                for group in [*groups, "overall"]:
                    rows = (
                        [r for values in occupation_rows.values() for r in values]
                        if group == "overall"
                        else occupation_rows[group]
                    )
                    values = historical_measures(rows)
                    municipality_counts = Counter(r["municipality_id"] for r in rows)
                    values.update(
                        {
                            "n_municipalities_with_ads": len(municipality_counts),
                            "n_municipalities_with_20plus_ads": sum(
                                n >= 20 for n in municipality_counts.values()
                            ),
                            "share_municipalities_with_ads": len(municipality_counts)
                            / county_sizes[region_id],
                            "standardised_share_required": sum(
                                weights[g] * rates[g] for g in groups
                            )
                            if cohort == "mapped_occupation_sensitivity"
                            and group == "overall"
                            and all(rates[g] is not None for g in groups)
                            else None,
                            "n_ads_5321": len(occupation_rows.get("5321", []))
                            if cohort == "mapped_occupation_sensitivity" and group == "overall"
                            else None,
                            "n_ads_5330": len(occupation_rows.get("5330", []))
                            if cohort == "mapped_occupation_sensitivity" and group == "overall"
                            else None,
                            "share_required_5321": rates.get("5321")
                            if group == "overall"
                            else None,
                            "share_required_5330": rates.get("5330")
                            if group == "overall"
                            else None,
                            "weight_5321": weights["5321"]
                            if cohort == "mapped_occupation_sensitivity" and group == "overall"
                            else None,
                            "weight_5330": weights["5330"]
                            if cohort == "mapped_occupation_sensitivity" and group == "overall"
                            else None,
                        }
                    )
                    if year not in processed_years:
                        values = dict.fromkeys(values)
                        values["coverage_flag"] = "NOT_PROCESSED"
                    result.append(
                        {
                            "region_id": region_id,
                            "region_name": name,
                            "year": year,
                            "historical_cohort": cohort,
                            "occupation_group": group,
                            "source_period": source_period(year),
                            "geography_basis": GEOGRAPHY,
                            "n_municipalities_in_county": county_sizes[region_id],
                            "analysis_status": "unvalidated_separate_sensitivity",
                            **values,
                        }
                    )
    return pd.DataFrame(result)
