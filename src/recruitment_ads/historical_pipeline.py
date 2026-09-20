"""Restartable historical sensitivity datasets, isolated from primary 2016–2025 outputs."""

import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .aggregate import measures
from .classify import classify_text_fields
from .deduplicate import assign_spells
from .download import iter_ads, sha256, write_json
from .employer import EmployerMaster
from .historical import (
    HISTORICAL_SCHEMA,
    VERSION,
    LegacyOccupations,
    attach_historical_cohort,
    classify_historical,
    role_candidate,
)
from .pipeline import read_config
from .schemas import encoded
from .validate import false_negative_metrics, metrics, preserve_review, validation_samples

COHORT_GROUPS = {
    "mapped_occupation_sensitivity": ["5321", "5330"],
    "unmapped_title_context_exploratory": [
        "title_underskoterska",
        "title_vardbitrade",
        "title_mixed_frontline",
    ],
}


def save_historical(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=HISTORICAL_SCHEMA), temp, compression="zstd")
    temp.replace(path)


def historical_spells(records, window=45, threshold=0.9):
    work = [
        {
            **r,
            "primary_eligible": r["historical_eligible"],
            "occupation_code": r["historical_cohort"] + ":" + str(r["historical_occupation_group"]),
        }
        for r in records
    ]
    clustered, representatives = assign_spells(work, window, threshold)
    by_id = {r["record_id"]: r for r in records}
    for row in clustered:
        by_id[row["record_id"]]["historical_recruitment_spell_id"] = row["recruitment_spell_id"]
        if row["duplicate_ad_id"]:
            by_id[row["record_id"]]["historical_eligible"] = False
    spells = [
        {
            **by_id[r["record_id"]],
            **{
                k: r[k]
                for k in ["n_ads_in_spell", "requirement_changed_within_spell", "spell_last_date"]
            },
            "spell_ad_ids": encoded(r["spell_ad_ids"]),
        }
        for r in representatives
    ]
    return records, spells


def historical_panel(records, master_rows, years, processed_years):
    grouped = defaultdict(list)
    for r in records:
        if r["historical_eligible"]:
            grouped[
                (
                    r["municipality_id"],
                    r["year"],
                    r["historical_cohort"],
                    r["historical_occupation_group"],
                )
            ].append(r)
    result = []
    for municipality in master_rows:
        for year in years:
            for cohort, groups in COHORT_GROUPS.items():
                for group in [*groups, "overall"]:
                    rows = (
                        [
                            r
                            for g in groups
                            for r in grouped.get(
                                (municipality["municipality_id"], year, cohort, g), []
                            )
                        ]
                        if group == "overall"
                        else grouped.get((municipality["municipality_id"], year, cohort, group), [])
                    )
                    values = historical_measures(rows)
                    if year not in processed_years:
                        values = dict.fromkeys(values)
                        values["coverage_flag"] = "NOT_PROCESSED"
                    result.append(
                        {
                            "municipality_id": municipality["municipality_id"],
                            "municipality_name": municipality["municipality_name"],
                            "year": year,
                            "historical_cohort": cohort,
                            "occupation_group": group,
                            "source_period": source_period(year),
                            "analysis_status": "unvalidated_separate_sensitivity",
                            **values,
                        }
                    )
    return pd.DataFrame(result)


def source_period(year):
    return (
        "2006_2015_original_legacy"
        if year <= 2015
        else "2016_2020_employer_id_gap"
        if year <= 2020
        else "2021_2025_reference"
    )


def historical_measures(rows):
    values = measures(
        [{**r, "recruitment_spell_id": r.get("historical_recruitment_spell_id")} for r in rows]
    )
    values.update(
        {
            "n_orgnr_exact": sum(r["employer_match_method"] == "orgnr_exact" for r in rows),
            "n_name_validated": sum(
                r["employer_match_method"] == "employer_name_validated" for r in rows
            ),
            "n_source_record_locators": sum(
                r.get("ad_identity_method") == "source_record_locator" for r in rows
            ),
        }
    )
    return values


def run_historical(
    config_path, master_path, aliases_path, taxonomy_path, cache, recent_output, output, years
):
    config = read_config(config_path)
    master = EmployerMaster.load(master_path, aliases_path)
    if len({r["municipality_id"] for r in master.rows}) != 290:
        raise ValueError("Historical execution requires all 290 municipal legal entities")
    output, recent_output = Path(output), Path(recent_output)
    if output.resolve() == recent_output.resolve():
        raise ValueError("Historical outputs must be separate from primary outputs")
    taxonomy_path = Path(taxonomy_path)
    taxonomy_source = (
        "https://taxonomy.api.jobtechdev.se/v1/taxonomy/legacy/get-occupation-name-with-relations"
    )
    crosswalk = LegacyOccupations(
        json.loads(taxonomy_path.read_text(encoding="utf-8")),
        taxonomy_source,
        sha256(taxonomy_path),
    )
    files = []
    for year in sorted(years):
        if not 2006 <= year <= 2015:
            raise ValueError("Older adapter accepts only 2006–2015 original archives")
        path = Path(cache) / f"{year}.jsonl.zip"
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        if not path.exists() or not manifest.exists():
            raise ValueError(f"Missing annual source or manifest: {year}")
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        if meta["source_year"] != year or meta["is_sample"] or sha256(path) != meta["source_hash"]:
            raise ValueError(f"Invalid historical source provenance: {year}")
        files.append((path, meta))
    identity = {
        "adapter_version": VERSION,
        "config": config,
        "years": sorted(years),
        "modules": {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "master_hash": sha256(master_path),
        "aliases_hash": sha256(aliases_path),
        "taxonomy_hash": sha256(taxonomy_path),
        "sources": [m for _, m in files],
        "recent_ads_hash": sha256(recent_output / "ads_classified.parquet"),
    }
    fingerprint = hashlib.sha256(encoded(identity).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "run_historical.json"
    if manifest.exists() and json.loads(manifest.read_text())["fingerprint"] != fingerprint:
        raise ValueError("Historical inputs/code changed; use a new output directory")
    write_json(manifest, {"fingerprint": fingerprint, **identity})
    snapshot = output / "reproduction" / "classification_source"
    snapshot.mkdir(parents=True, exist_ok=True)
    for path in Path(__file__).parent.glob("*.py"):
        shutil.copy2(path, snapshot / path.name)
    for path in (config_path, master_path, aliases_path, taxonomy_path):
        shutil.copy2(path, output / "reproduction" / Path(path).name)
    patterns = [
        (
            r["municipality_id"],
            re.compile(r"^" + re.escape(r["municipality_name"]) + r"s?\s+(?:kommun|stad)\b", re.I),
        )
        for r in master.rows
    ]

    @lru_cache(maxsize=100000)
    def candidate(name):
        found = {code for code, pattern in patterns if pattern.search(name or "")}
        return next(iter(found)) if len(found) == 1 else None

    all_rows, inventories = [], []
    for path, source in files:
        year = source["source_year"]
        shard = output / "checkpoints" / f"{year}.parquet"
        inventory_path = shard.with_suffix(".json")
        if shard.exists() and inventory_path.exists():
            inventory = json.loads(inventory_path.read_text())
            if sha256(shard) != inventory["parquet_hash"]:
                raise ValueError("Historical checkpoint checksum mismatch")
            rows = pq.read_table(shard).to_pylist()
        else:
            rows, counts, months = [], Counter(), Counter()
            for raw, member, line in iter_ads(path):
                counts["source_records"] += 1
                emp = raw.get("employer") or {}
                publication = raw.get("publication_date") or ""
                counts["missing_source_ad_id"] += not bool(raw.get("id") or raw.get("original_id"))
                counts["missing_employer_orgnr"] += not bool(emp.get("organization_number"))
                counts["missing_source_ssyk"] += not bool(json.loads(occupation_codes(raw)))
                if publication[:4] != str(year):
                    counts["publication_year_mismatch_or_missing"] += 1
                    continue
                months[publication[5:7]] += 1
                match = master.match(
                    emp.get("organization_number"), emp.get("name"), publication[:10]
                )
                potential = (
                    candidate(emp.get("name"))
                    if match["employer_match_method"] == "unresolved"
                    else None
                )
                if not match["municipality_id"] and not potential:
                    continue
                counts["municipal_employer_or_name_candidate"] += 1
                if not role_candidate(raw, crosswalk):
                    continue
                ad = classify_historical(raw, source, master, config, crosswalk, member, line)
                ad["candidate_employer_municipality_id"] = potential
                rows.append(ad)
                counts["retained_candidates"] += 1
                counts["cohort_" + ad["historical_cohort"]] += 1
                counts["mapping_" + ad["occupation_mapping_status"]] += 1
            save_historical(shard, rows)
            inventory = {
                "year": year,
                "source": source,
                "counts": dict(counts),
                "publication_month_counts": dict(months),
                "parquet_hash": sha256(shard),
            }
            write_json(inventory_path, inventory)
        all_rows.extend(rows)
        inventories.append(inventory)
        print(
            f"{year}: {inventory['counts']['source_records']} source records; {len(rows)} retained",
            flush=True,
        )
    recent_inventory = json.loads((recent_output / "source_inventory.json").read_text())
    for original in pq.read_table(recent_output / "ads_classified.parquet").to_pylist():
        row = dict(original)
        # The extension and its reference years use the same language-rule version.
        # The original primary output remains immutable at its earlier classifier version.
        classify_text_fields(row, config)
        row.update(
            {
                "source_ad_id": row["ad_id"],
                "ad_identity_method": "official_ad_id",
                "original_legacy_attributes_json": None,
                "raw_record_hash": None,
            }
        )
        mapping = {
            "occupation_mapping_status": "source_structured_ssyk"
            if json.loads(row["occupation_codes_json"])
            else "unmapped_legacy_code",
            "legacy_occupation_ids_json": encoded(
                [
                    str(v.get("legacy_ams_taxonomy_id"))
                    for v in json.loads(row["original_occupation_json"] or "[]")
                ]
            )
            if isinstance(json.loads(row["original_occupation_json"] or "[]"), list)
            else encoded([]),
            "occupation_mapping_source": row["source_url"],
            "occupation_mapping_hash": row["source_hash"],
        }
        all_rows.append(attach_historical_cohort(row, mapping))
    records, spells = historical_spells(
        all_rows, config["reposting_window_days"], config["text_similarity_threshold"]
    )
    save_historical(output / "ads_historical.parquet", records)
    spell_schema = pa.schema(
        list(HISTORICAL_SCHEMA)
        + [
            ("n_ads_in_spell", pa.int64()),
            ("requirement_changed_within_spell", pa.bool_()),
            ("spell_last_date", pa.string()),
            ("spell_ad_ids", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(spells, schema=spell_schema),
        output / "recruitment_spells_historical.parquet",
        compression="zstd",
    )
    write_json(output / "historical_source_inventory.json", inventories)
    write_json(output / "recent_source_inventory.json", recent_inventory)
    export_historical(records, spells, master.rows, inventories, recent_inventory, output, config)
    return records, spells


def occupation_codes(raw):
    from .occupations import occupation

    return occupation(raw)["occupation_codes_json"]


def export_historical(records, spells, master, inventories, recent_inventory, output, config):
    processed = {r["year"] for r in inventories + recent_inventory}
    panel = historical_panel(records, master, range(2006, 2026), processed)
    panel.to_parquet(output / "municipality_year_historical.parquet", index=False)
    spell_panel = historical_panel(spells, master, range(2006, 2026), processed)
    spell_panel.to_parquet(output / "municipality_year_spells_historical.parquet", index=False)
    national, occupations, spell_trends = [], [], []
    for year in sorted(processed):
        for cohort, groups in COHORT_GROUPS.items():
            rows = [
                r
                for r in records
                if r["year"] == year
                and r["historical_eligible"]
                and r["historical_cohort"] == cohort
            ]
            national.append(
                {
                    "year": year,
                    "historical_cohort": cohort,
                    "source_period": source_period(year),
                    **historical_measures(rows),
                }
            )
            for group in groups:
                occupations.append(
                    {
                        "year": year,
                        "historical_cohort": cohort,
                        "occupation_group": group,
                        **historical_measures(
                            [r for r in rows if r["historical_occupation_group"] == group]
                        ),
                    }
                )
            rates = {
                g: historical_measures([r for r in rows if r["historical_occupation_group"] == g])[
                    "share_required_ads"
                ]
                for g in groups
            }
            weights = config["fixed_occupation_weights"]
            national[-1]["standardised_share_required"] = (
                sum(weights[g] * rates[g] for g in groups)
                if cohort == "mapped_occupation_sensitivity"
                and all(rates[g] is not None for g in groups)
                else None
            )
            national[-1]["fixed_occupation_weights"] = (
                encoded(weights) if cohort == "mapped_occupation_sensitivity" else None
            )
            spell_trends.append(
                {
                    "year": year,
                    "historical_cohort": cohort,
                    **historical_measures(
                        [
                            r
                            for r in spells
                            if r["year"] == year and r["historical_cohort"] == cohort
                        ]
                    ),
                }
            )
    review_population = [
        {
            **r,
            "primary_eligible": r["historical_eligible"],
            "sensitivity_eligible": False,
            "occupation_code": r["historical_occupation_group"],
        }
        for r in records
        if r["year"] <= 2020 and r["historical_eligible"]
    ]
    review, negatives, sampling = validation_samples(
        review_population, config["validation_quotas"], config["seed"], 300
    )
    by_id = {r["record_id"]: r for r in records}
    for sample in (review, negatives):
        for key in [
            "primary_eligible",
            "occupation_code",
            "historical_cohort",
            "historical_occupation_group",
            "ad_identity_method",
            "occupation_mapping_status",
            "employer_name",
        ]:
            sample[key] = [by_id[rid].get(key) for rid in sample.record_id]
    review = preserve_review(output / "validation.csv", review)
    negatives = preserve_review(output / "false_negatives.csv", negatives)
    sampling.to_csv(output / "validation_sampling_inventory.csv", index=False)
    frame = pd.DataFrame(records)
    employer = (
        frame.groupby(
            [
                "year",
                "employer_match_method",
                "employer_name",
                "municipality_id",
                "candidate_employer_municipality_id",
            ],
            dropna=False,
        )
        .size()
        .reset_index(name="n_candidates")
    )
    employer.to_csv(output / "employer_review_inventory.csv", index=False)
    occupation_inventory = (
        frame.groupby(
            [
                "year",
                "legacy_occupation_ids_json",
                "occupation_mapping_status",
                "occupation_code",
                "historical_occupation_group",
            ],
            dropna=False,
        )
        .size()
        .reset_index(name="n_candidates")
    )
    occupation_inventory.to_csv(output / "occupation_mapping_inventory.csv", index=False)
    # Small reproducible review of excluded employer/context/occupation cases, never gold labels.
    excluded = sorted(
        [r for r in records if r["year"] <= 2020 and not r["historical_eligible"]],
        key=lambda r: r["record_id"],
    )
    chosen = random.Random(config["seed"]).sample(excluded, min(150, len(excluded)))
    exclusion_review = pd.DataFrame(
        [
            {
                k: r.get(k)
                for k in [
                    "record_id",
                    "ad_id",
                    "text_hash",
                    "year",
                    "employer_name",
                    "employer_match_method",
                    "candidate_employer_municipality_id",
                    "original_job_title",
                    "legacy_occupation_ids_json",
                    "occupation_mapping_status",
                    "historical_context",
                    "description_text",
                    "source_url",
                ]
            }
            | {"manual_category": "", "notes": ""}
            for r in chosen
        ],
        columns=[
            "record_id",
            "ad_id",
            "text_hash",
            "year",
            "employer_name",
            "employer_match_method",
            "candidate_employer_municipality_id",
            "original_job_title",
            "legacy_occupation_ids_json",
            "occupation_mapping_status",
            "historical_context",
            "description_text",
            "source_url",
            "manual_category",
            "notes",
        ],
    )
    exclusion_review = preserve_review(output / "excluded_cases_review.csv", exclusion_review)
    audit = pd.DataFrame(
        [
            {
                "year": r["year"],
                "source_url": r["source"]["source_url"],
                "source_hash": r["source"]["source_hash"],
                **r["counts"],
            }
            for r in inventories
        ]
    )
    methodology = pd.DataFrame(
        [
            {
                "topic": "Construct",
                "definition": "Swedish-language recruitment wording only. No formal municipal policy inference.",
            },
            {
                "topic": "Mapped sensitivity",
                "definition": "Known municipal legal name or exact organisation number, official structured SSYK/crosswalk 5321/5330, explicit elderly-care job context. Separate from the primary dataset.",
            },
            {
                "topic": "Title exploratory",
                "definition": "Unmapped legacy occupation with explicit undersköterska/vårdbiträde title and elderly-care context. No modern SSYK code is imputed. Kept separate from mapped estimates.",
            },
            {
                "topic": "Employer",
                "definition": "Only exact validated aliases from the official current legal-entity register qualify as name fallback. Unresolved department/brand names are retained for review, never identified from workplace geography.",
            },
            {
                "topic": "Historical identity",
                "definition": "Missing official ad IDs receive source-record locators from archive hash/member/line. They identify records, not recovered advertisements. Exact row repeats may represent duplicate source records; spell sensitivity is separate.",
            },
            {
                "topic": "Comparability",
                "definition": "2006–2015 original archives, 2016–2020 employer-number gaps and 2021–2025 reference ads remain labelled. Crosswalk is a current official snapshot, not proof of historical coding stability. No merged primary trend is produced.",
            },
            {
                "topic": "Municipality keys",
                "definition": "Current-master municipality IDs identify municipal legal entities across time. Historical name/organisation continuity is unverified; original workplace codes remain separate. No historical geography is inferred.",
            },
            {
                "topic": "Coverage",
                "definition": "NO_ADS yields missing shares. Unprocessed years remain missing. Positive context is required consistently in all extension/reference years. Context boilerplate and employer-name changes may affect selection.",
            },
            {
                "topic": "Validation",
                "definition": "600 stratified older/gap-period cases, 300 random eligible negatives and 150 excluded-case reviews. All human coding starts blank. Classifier performance is unknown until reviewed.",
            },
            {
                "topic": "Counts and vacancies",
                "definition": "Ad/source-record weighted estimates; positive known vacancy counts only, with missing-as-one sensitivity. Both raw records and bounded 45-day recruitment spells are retained.",
            },
        ]
    )
    eligible_frame = frame[frame.historical_eligible]
    af = (
        eligible_frame.groupby(
            ["year", "historical_cohort", "swedish_requirement", "af_must_have_swedish"],
            dropna=False,
        )
        .size()
        .reset_index(name="n_ads")
    )
    discordant = eligible_frame[
        eligible_frame.af_must_have_swedish.notna()
        & (eligible_frame.swedish_requirement != eligible_frame.af_must_have_swedish)
    ]
    discordant[
        [
            "record_id",
            "ad_id",
            "year",
            "historical_cohort",
            "employer_name",
            "original_job_title",
            "swedish_requirement",
            "af_must_have_swedish",
            "description_text",
            "source_url",
        ]
    ].to_csv(output / "af_discordant_historical.csv", index=False)
    tables = {
        "Municipality_Year": panel[panel.occupation_group == "overall"],
        "National_Trends": pd.DataFrame(national),
        "Occupation_Trends": pd.DataFrame(occupations),
        "Language_Categories": pd.DataFrame(national)[
            ["year", "historical_cohort", *[k for k in national[0] if k.startswith("share_")]]
        ],
        "Validation": metrics(review),
        "False_Negatives": false_negative_metrics(negatives),
        "AF_Comparison": af,
        "Coverage": panel[panel.occupation_group == "overall"][
            [
                "municipality_id",
                "municipality_name",
                "year",
                "historical_cohort",
                "n_ads",
                "n_recruitment_spells",
                "coverage_flag",
            ]
        ],
        "Employer_Matches": frame.groupby(["year", "employer_match_method"], dropna=False)
        .size()
        .reset_index(name="n_candidates"),
        "Occupation_Mapping": occupation_inventory,
        "Methodology": methodology,
        "Audit": audit,
        "Spell_Trends": pd.DataFrame(spell_trends),
    }
    reviews = {
        "Validation": review,
        "False_Negatives": negatives,
        "Excluded_Cases": exclusion_review,
        "Instructions": pd.DataFrame(
            [
                {
                    "instruction": "Read full original text in the cell editor or CSV; displayed row height is a preview. Code manual_required/manual_formal only after adjudication. Legacy title-only cases are exploratory, never modern SSYK imputations. Preserve source IDs and hashes. Recruitment wording cannot establish policy."
                }
            ]
        ),
    }
    (output / "tables").mkdir(exist_ok=True)
    for name, table in tables.items():
        table.to_csv(output / "tables" / f"{name}.csv", index=False)
    write_json(
        output / "workbook_tables.json",
        {k: json.loads(v.to_json(orient="records", force_ascii=False)) for k, v in tables.items()},
    )
    write_json(
        output / "review_workbook_tables.json",
        {k: json.loads(v.to_json(orient="records", force_ascii=False)) for k, v in reviews.items()},
    )
    write_json(
        output / "audit_historical.json",
        {
            "adapter_version": VERSION,
            "historical_source_records": sum(r["counts"]["source_records"] for r in inventories),
            "retained_2006_2015": sum(r["year"] <= 2015 for r in records),
            "retained_2016_2020": sum(2016 <= r["year"] <= 2020 for r in records),
            "eligible_by_period_cohort": dict(
                Counter(
                    source_period(r["year"]) + ":" + r["historical_cohort"]
                    for r in records
                    if r["historical_eligible"]
                )
            ),
            "historical_spells": len(spells),
            "review_sample": len(review),
            "negative_sample": len(negatives),
            "excluded_review_sample": len(exclusion_review),
            "human_validation": "pending",
            "primary_dataset_changed": False,
            "combined_primary_trend": False,
        },
    )
