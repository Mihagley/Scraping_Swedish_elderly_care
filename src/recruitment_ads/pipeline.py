"""Stream official yearly files, checkpoint by content and code hash, retain pilot scope."""

import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from .classify import classify_ad
from .deduplicate import assign_spells
from .download import iter_ads, sha256, write_json
from .employer import EmployerMaster
from .occupations import occupation
from .schemas import SCHEMA, encoded


def read_config(path):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if config.get("historical_extension_enabled"):
        raise ValueError("2006–2015 require a separate reviewed adapter and comparability study")
    if config.get("llm_enabled"):
        raise ValueError(
            "This version uses deterministic semantics and manual review; LLM is not configured"
        )
    return config


def save_ads(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), temp, compression="zstd")
    temp.replace(path)


def run_classification(
    config_path, master_path, aliases_path, cache, output, years, municipality_ids=None
):
    config = read_config(config_path)
    master = EmployerMaster.load(master_path, aliases_path)
    if len({r["municipality_id"] for r in master.rows}) != 290:
        raise ValueError("Production processing requires a complete 290-municipality master")
    scope = set(municipality_ids or [r["municipality_id"] for r in master.rows])
    if not scope <= {r["municipality_id"] for r in master.rows}:
        raise ValueError("Unknown municipality in scope")
    candidate_patterns = [
        (
            r["municipality_id"],
            re.compile(r"^" + re.escape(r["municipality_name"]) + r"s?\s+(?:kommun|stad)\b", re.I),
        )
        for r in master.rows
        if r["municipality_id"] in scope
    ]

    @lru_cache(maxsize=100000)
    def candidate_employer(name):
        candidates = [code for code, pattern in candidate_patterns if pattern.search(name or "")]
        return candidates[0] if len(candidates) == 1 else None

    files = []
    for meta_path in sorted(Path(cache).glob("*.manifest.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("source_year") in years:
            path = meta_path.with_name(meta["source_file"])
            if sha256(path) != meta["source_hash"]:
                raise ValueError(f"Source checksum changed: {path}")
            files.append((path, meta))
    if len({m["source_year"] for _, m in files}) != len(files):
        raise ValueError(
            "Use one annual source per year; do not mix full and sample/enriched versions"
        )
    if not files:
        raise ValueError("No matching downloaded annual files")
    module_hashes = {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    identity = {
        "config": config,
        "modules": module_hashes,
        "master_hash": sha256(master_path),
        "aliases_hash": sha256(aliases_path) if aliases_path else None,
        "scope": sorted(scope),
        "requested_years": years,
        "sources": [m for _, m in files],
    }
    fingerprint = hashlib.sha256(encoded(identity).encode()).hexdigest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_path = output / "run.json"
    if run_path.exists() and json.loads(run_path.read_text())["fingerprint"] != fingerprint:
        raise ValueError(
            "Inputs/code/config changed. Use a new output directory; existing results are immutable."
        )
    write_json(run_path, {"fingerprint": fingerprint, **identity})
    all_rows, inventories = [], []
    for path, source in files:
        year = source["source_year"]
        if year < 2016:
            raise ValueError("Historical extension is not comparable by default")
        shard = output / "checkpoints" / f"{year}.parquet"
        inventory_path = shard.with_suffix(".json")
        if shard.exists() and inventory_path.exists():
            inventory = json.loads(inventory_path.read_text())
            if inventory.get("parquet_hash") != sha256(shard):
                raise ValueError("Checkpoint checksum mismatch")
            rows = pq.read_table(shard).to_pylist()
        else:
            rows, counts, missing, months = [], Counter(), Counter(), Counter()
            for raw, member, line in iter_ads(path):
                counts["source_records"] += 1
                for field in (
                    "id",
                    "publication_date",
                    "employer",
                    "occupation_group",
                    "description",
                    "must_have",
                    "nice_to_have",
                    "number_of_vacancies",
                ):
                    if raw.get(field) is None:
                        missing[field] += 1
                emp = raw.get("employer") or {}
                if not emp.get("organization_number"):
                    missing["employer.organization_number"] += 1
                publication = raw.get("publication_date") or ""
                if publication[:4] != str(year):
                    counts["publication_year_mismatch_or_missing"] += 1
                    continue
                months[publication[5:7]] += 1
                match = master.match(
                    emp.get("organization_number"), emp.get("name"), publication[:10]
                )
                counts["employer_" + match["employer_match_method"]] += 1
                potential_id = (
                    candidate_employer(emp.get("name"))
                    if match["employer_match_method"] == "unresolved"
                    else None
                )
                if match["municipality_id"] not in scope and not potential_id:
                    continue
                counts[
                    "potential_scope_employer" if potential_id else "matched_scope_employer"
                ] += 1
                if not occupation(raw, tuple(config["occupations"]))["occupation_candidate"]:
                    counts["outside_occupation_population"] += 1
                    continue
                ad = classify_ad(raw, source, master, config, member, line)
                ad["candidate_employer_municipality_id"] = potential_id
                counts["retained_candidates"] += 1
                counts["eligibility_" + ad["eligibility_reason"]] += 1
                rows.append(ad)
            save_ads(shard, rows)
            inventory = {
                "year": year,
                "source": source,
                "counts": dict(counts),
                "field_missing": dict(missing),
                "publication_month_counts": dict(months),
                "parquet_hash": sha256(shard),
            }
            write_json(inventory_path, inventory)
        inventories.append(inventory)
        all_rows.extend(rows)
        print(
            f"{year}: {inventory['counts']['source_records']} source records, {len(rows)} retained",
            flush=True,
        )
    ads, spells = assign_spells(
        all_rows, config["reposting_window_days"], config["text_similarity_threshold"]
    )
    save_ads(output / "ads_classified.parquet", ads)
    # Spell extensions use JSON for membership, with the same original ad fields as representative.
    spell_schema = pa.schema(
        list(SCHEMA)
        + [
            pa.field("n_ads_in_spell", pa.int64()),
            pa.field("requirement_changed_within_spell", pa.bool_()),
            pa.field("spell_last_date", pa.string()),
            pa.field("spell_ad_ids", pa.string()),
        ]
    )
    for spell in spells:
        spell["spell_ad_ids"] = encoded(spell["spell_ad_ids"])
    pq.write_table(
        pa.Table.from_pylist(spells, schema=spell_schema),
        output / "recruitment_spells.parquet",
        compression="zstd",
    )
    hit_rows = [
        {"record_id": r["record_id"], "ad_id": r["ad_id"], **h}
        for r in ads
        for h in json.loads(r["language_hits_json"])
    ]
    hit_schema = pa.schema(
        [
            (k, pa.string())
            for k in (
                "record_id",
                "ad_id",
                "category",
                "status",
                "rule_id",
                "matched_phrase",
                "matched_sentence",
                "context_before",
                "context_after",
            )
        ]
        + [(k, pa.int64()) for k in ("start", "end")]
    )
    pq.write_table(
        pa.Table.from_pylist(hit_rows, schema=hit_schema), output / "language_hits.parquet"
    )
    write_json(output / "source_inventory.json", inventories)
    write_json(
        output / "audit.json",
        {
            "fingerprint": fingerprint,
            "retained_ads": len(ads),
            "primary_eligible_ads": sum(r["primary_eligible"] for r in ads),
            "eligible_spells": len(spells),
            "manual_validation": "pending",
            "historical_extension": "disabled",
            "inference": "Recruitment wording only. No formal municipal policy status is inferred.",
        },
    )
    return ads, spells
