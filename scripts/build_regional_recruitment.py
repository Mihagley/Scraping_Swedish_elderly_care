"""Build county trends from an existing frozen historical recruitment run."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from recruitment_ads.aggregate import CATEGORY_MEASURES
from recruitment_ads.download import sha256, write_json
from recruitment_ads.export import export_xlsx
from recruitment_ads.pipeline import read_config
from recruitment_ads.regions import GEOGRAPHY, SCB_URL, VERSION, county_mapping, regional_panel


def build(source, output, scb_html, *, workbook=True):
    source, output, scb_html = Path(source), Path(output), Path(scb_html)
    if source.resolve() == output.resolve():
        raise ValueError("County outputs must have their own directory")
    config = read_config(source / "reproduction/config.yaml")
    master_path = source / "reproduction/employer_master.csv"
    master = pd.read_csv(master_path, dtype=str, keep_default_na=False).to_dict("records")
    mapping = county_mapping(scb_html, master)
    manifests = [
        json.loads((source / name).read_text())
        for name in ["historical_source_inventory.json", "recent_source_inventory.json"]
    ]
    processed = {item["year"] for items in manifests for item in items}
    if not set(range(2006, 2026)).issuperset(processed):
        raise ValueError("Regional series accepts only 2006–2025 sources")
    files = [
        "ads_historical.parquet",
        "recruitment_spells_historical.parquet",
        "run_historical.json",
        "tables/National_Trends.csv",
    ]
    identity = {
        "aggregation_version": VERSION,
        "source_files": {name: sha256(source / name) for name in files},
        "mapping_source_url": SCB_URL,
        "mapping_source_hash": sha256(scb_html),
        "master_hash": sha256(master_path),
        "geography_basis": GEOGRAPHY,
        "fixed_occupation_weights": config["fixed_occupation_weights"],
        "modules": {
            name: sha256(Path(__file__).parents[1] / "src/recruitment_ads" / name)
            for name in ["regions.py", "aggregate.py", "historical_pipeline.py"]
        },
        "script_hash": sha256(__file__),
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    run = output / "run_regional.json"
    if run.exists() and json.loads(run.read_text())["fingerprint"] != fingerprint:
        raise ValueError("Regional inputs or code changed; use a new output directory")
    columns = list(
        dict.fromkeys(
            [
                "municipality_id",
                "year",
                "historical_cohort",
                "historical_occupation_group",
                "historical_eligible",
                "duplicate_ad_id",
                "swedish_requirement",
                "swedish_preferred",
                "number_of_vacancies",
                "historical_recruitment_spell_id",
                "classification_status",
                "employer_match_method",
                "ad_identity_method",
                *CATEGORY_MEASURES.values(),
            ]
        )
    )
    panels = []
    for filename in files[:2]:
        records = pq.read_table(source / filename, columns=columns).to_pylist()
        panels.append(
            regional_panel(
                records, mapping, range(2006, 2026), processed, config["fixed_occupation_weights"]
            )
        )
    panel, spells = panels
    national = pd.read_csv(source / "tables/National_Trends.csv")
    overall = panel[panel.occupation_group == "overall"]
    pooled = overall.groupby(["year", "historical_cohort"])[
        ["n_ads", "n_required_swedish", "n_preferred_swedish"]
    ].sum(min_count=1)
    expected = national.set_index(["year", "historical_cohort"])[pooled.columns].sort_index()
    pd.testing.assert_frame_equal(
        pooled.loc[expected.index].sort_index(), expected, check_dtype=False
    )
    assert overall.n_ads.sum() == national.n_ads.sum()
    mapping_table = pd.DataFrame(mapping)
    mapping_table["source_hash"] = identity["mapping_source_hash"]
    methodology = pd.DataFrame(
        [
            {
                "topic": "Region",
                "definition": "Swedish county (län), assigned from the matched municipal employer's current municipality key. Regional-government employers are not added.",
                "source_url": SCB_URL,
            },
            {
                "topic": "Fixed geography",
                "definition": "SCB 2026 county membership is held fixed in every year. Historical boundary changes are not reconstructed. Heby is grouped under current Uppsala county throughout.",
                "source_url": SCB_URL,
            },
            {
                "topic": "Numerator and denominator",
                "definition": "Eligible ads classified as explicitly requiring Swedish divided by all eligible ads in the county/year/cohort. Municipal percentages are not averaged. Each ad counts once; source-record IDs remain locators where official IDs are absent.",
                "source_url": "https://data.jobtechdev.se/annonser/historiska/",
            },
            {
                "topic": "Separate cohorts",
                "definition": "Mapped occupations and unmapped-title exploratory cases remain separate. Neither is merged into the frozen primary dataset. Original, employer-ID-gap and recent-reference periods remain identified.",
                "source_url": "https://taxonomy.api.jobtechdev.se/v1/taxonomy/legacy/get-occupation-name-with-relations",
            },
            {
                "topic": "Fixed occupation weights",
                "definition": "The standardised rate uses 0.5 for SSYK 5321 and 0.5 for 5330 by default, read from the frozen run config. It remains missing if either occupation has no denominator. The title-only cohort has no standardised rate.",
                "source_url": "",
            },
            {
                "topic": "Coverage",
                "definition": "No-ad rates stay missing. Coverage_flag indicates record volume (20+, 5–19, 1–4, 0), not representative coverage. County municipality counts and the share with observed ads are reported separately. Sparse plotted points are hollow.",
                "source_url": "",
            },
            {
                "topic": "Spells and vacancies",
                "definition": "Spell tables use first-record representatives. An annual raw-ad spell count can include a cross-year spell in both years. Vacancy weighting uses known positive counts; older missing counts stay missing, with missing-as-one sensitivity separately labelled.",
                "source_url": "",
            },
            {
                "topic": "Interpretation",
                "definition": "Classifier accuracy and historical selection comparability remain unvalidated. County differences may reflect employer-name availability, occupation coding and the municipalities observed. Recruitment wording does not establish formal municipal policy.",
                "source_url": "",
            },
        ]
    )
    tables = {
        "Region_Year": overall.reset_index(drop=True),
        "Region_Occupation": panel[panel.occupation_group != "overall"].reset_index(drop=True),
        "Spell_Trends": spells[spells.occupation_group == "overall"].reset_index(drop=True),
        "Coverage": overall[
            [
                "region_id",
                "region_name",
                "year",
                "historical_cohort",
                "n_ads",
                "n_recruitment_spells",
                "coverage_flag",
                "n_municipalities_in_county",
                "n_municipalities_with_ads",
                "n_municipalities_with_20plus_ads",
                "share_municipalities_with_ads",
            ]
        ].reset_index(drop=True),
        "Municipality_County": mapping_table,
        "Methodology": methodology,
        "Sources": pd.DataFrame(
            [
                {"source": name, "sha256": digest, "description": "Frozen historical-run input"}
                for name, digest in identity["source_files"].items()
            ]
            + [
                {
                    "source": SCB_URL,
                    "sha256": identity["mapping_source_hash"],
                    "description": "Official 2026 county/municipality snapshot",
                }
            ]
        ),
    }
    panel.to_parquet(output / "region_year.parquet", index=False)
    spells.to_parquet(output / "region_year_spells.parquet", index=False)
    (output / "tables").mkdir(exist_ok=True)
    for name, table in tables.items():
        table.to_csv(output / "tables" / f"{name}.csv", index=False)
    write_json(
        output / "workbook_tables.json",
        {
            name: json.loads(table.to_json(orient="records", force_ascii=False))
            for name, table in tables.items()
        },
    )
    write_json(
        run,
        {
            "fingerprint": fingerprint,
            **identity,
            "n_regions": len({r["region_id"] for r in mapping}),
            "n_municipalities": len(mapping),
            "eligible_records": int(overall.n_ads.sum()),
            "spell_representatives": int(spells[spells.occupation_group == "overall"].n_ads.sum()),
            "panel_rows": len(panel),
            "reconciles_to_national_counts": True,
            "input_classifier_version": config["classifier_version"],
        },
    )
    (output / "provenance").mkdir(exist_ok=True)
    shutil.copy2(scb_html, output / "provenance/scb-municipalities.html")
    shutil.copy2(source / "run_historical.json", output / "provenance/run_historical.json")
    shutil.copy2(source / "reproduction/config.yaml", output / "provenance/config.yaml")
    if workbook:
        export_xlsx(tables, output / "regional_language_requirements.xlsx")
    print(
        json.dumps(
            {
                "regions": 21,
                "municipalities": 290,
                "panel_rows": len(panel),
                "eligible_records": int(overall.n_ads.sum()),
                "spell_representatives": int(
                    spells[spells.occupation_group == "overall"].n_ads.sum()
                ),
            }
        )
    )
    return tables


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="research/recruitment_ads/output-historical-v2")
    p.add_argument("--output", default="research/recruitment_ads/output-regions-v2")
    p.add_argument(
        "--scb-html",
        default="research/recruitment_ads/cache/employer_sources/scb-municipalities.html",
    )
    p.add_argument("--tables-only", action="store_true")
    args = p.parse_args()
    build(args.source, args.output, args.scb_html, workbook=not args.tables_only)


if __name__ == "__main__":
    main()
