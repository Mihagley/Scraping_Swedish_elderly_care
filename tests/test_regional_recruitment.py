from pathlib import Path

import pandas as pd
import pytest

from recruitment_ads.aggregate import CATEGORY_MEASURES
from recruitment_ads.regions import GEOGRAPHY, county_mapping, regional_panel

MAPPING = [
    {"municipality_id": "0114", "region_id": "01", "region_name": "Stockholms län"},
    {"municipality_id": "0180", "region_id": "01", "region_name": "Stockholms län"},
    {"municipality_id": "0331", "region_id": "03", "region_name": "Uppsala län"},
]


def ad(municipality="0114", occupation="5321", required=False, **changes):
    return {
        "municipality_id": municipality,
        "year": 2010,
        "historical_cohort": "mapped_occupation_sensitivity",
        "historical_occupation_group": occupation,
        "historical_eligible": True,
        "duplicate_ad_id": False,
        "historical_recruitment_spell_id": municipality + occupation,
        "swedish_requirement": required,
        "swedish_preferred": False,
        "number_of_vacancies": None,
        "classification_status": "deterministic",
        "employer_match_method": "employer_name_validated",
        "ad_identity_method": "source_record_locator",
        **dict.fromkeys(CATEGORY_MEASURES.values(), False),
        **changes,
    }


def overall(panel, region="01", year=2010, cohort="mapped_occupation_sensitivity"):
    return panel[
        (panel.region_id == region)
        & (panel.year == year)
        & (panel.historical_cohort == cohort)
        & (panel.occupation_group == "overall")
    ].iloc[0]


def test_region_pools_ads_and_standardises_occupations_separately():
    records = [ad() for _ in range(90)] + [ad("0180", "5330", True) for _ in range(10)]
    row = overall(regional_panel(records, MAPPING, [2010], {2010}))
    assert row.n_ads == 100 and row.n_required_swedish == 10
    assert row.share_required_ads == 0.1  # Not the unweighted municipality mean, 0.5.
    assert row.standardised_share_required == 0.5
    assert row.n_municipalities_with_ads == 2
    assert row.n_municipalities_with_20plus_ads == 1
    assert row.share_required_vacancies is None or pd.isna(row.share_required_vacancies)


def test_no_ads_and_unprocessed_county_cells_remain_distinct():
    panel = regional_panel([ad()], MAPPING, [2010, 2011], {2010})
    empty = overall(panel, region="03")
    assert empty.n_ads == 0 and empty.coverage_flag == "NO_ADS"
    assert pd.isna(empty.share_required_ads)
    missing = overall(panel, year=2011)
    assert pd.isna(missing.n_ads) and missing.coverage_flag == "NOT_PROCESSED"
    assert pd.isna(overall(panel).standardised_share_required)  # No 5330 denominator.


def test_unmapped_cohort_is_not_added_to_mapped_denominator():
    title = ad(
        required=True,
        historical_cohort="unmapped_title_context_exploratory",
        historical_occupation_group="title_underskoterska",
    )
    panel = regional_panel([ad(), title], MAPPING, [2010], {2010})
    assert overall(panel).n_ads == 1 and overall(panel).share_required_ads == 0
    exploratory = overall(panel, cohort="unmapped_title_context_exploratory")
    assert exploratory.share_required_ads == 1
    assert pd.isna(exploratory.standardised_share_required)


def test_region_uses_employer_key_and_excludes_duplicate_or_ineligible_rows():
    records = [
        ad("0331", workplace_municipality="0180"),
        ad(duplicate_ad_id=True),
        ad(historical_eligible=False),
    ]
    panel = regional_panel(records, MAPPING, [2010], {2010})
    assert overall(panel, region="03").n_ads == 1
    assert overall(panel, region="03").geography_basis == GEOGRAPHY
    assert overall(panel).n_ads == 0
    assert records[0]["municipality_id"] == "0331"


def test_unmapped_county_and_invalid_weights_raise():
    with pytest.raises(ValueError, match="no county mapping"):
        regional_panel([ad("9999")], MAPPING, [2010], {2010})
    with pytest.raises(ValueError, match="sum to one"):
        regional_panel([], MAPPING, [2010], {2010}, {"5321": 1, "5330": 1})


def test_official_county_parser_handles_all_municipality_keys(tmp_path):
    root = Path(__file__).parents[1]
    master = pd.read_csv(root / "research/recruitment_ads/employer_master.csv", dtype=str).to_dict(
        "records"
    )
    html = []
    for county in sorted({r["municipality_id"][:2] for r in master}):
        html.append(f"<h2>{county} County län</h2>")
        html.extend(
            f"<p>{r['municipality_id']} {r['municipality_name']}</p>"
            for r in master
            if r["municipality_id"].startswith(county)
        )
    source = tmp_path / "scb.html"
    source.write_text("\n".join(html), encoding="utf-8")
    result = county_mapping(source, master)
    assert len(result) == 290 and len({r["region_id"] for r in result}) == 21
    assert next(r for r in result if r["municipality_id"] == "0331")["region_id"] == "03"
    with pytest.raises(ValueError, match="identical keys"):
        county_mapping(source, master[:-1])
