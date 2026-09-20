"""Deterministic review samples and observed, never invented, validation metrics."""

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


def period(year):
    return (
        "2006–2010"
        if year <= 2010
        else "2011–2015"
        if year <= 2015
        else "2016–2018"
        if year <= 2018
        else "2019–2021"
        if year <= 2021
        else "2022–2025"
        if year <= 2025
        else "2026_incomplete"
    )


def stratum(row):
    hits = json.loads(row["language_hits_json"])
    if any(
        row[k + "_hit"]
        for k in (
            "gers_b1",
            "gers_b2",
            "other_cefr_level",
            "svenska_1",
            "svenska_som_andrasprak_1",
            "sva_1",
            "svenska_a",
            "svenska_som_andrasprak_a",
            "sfi_a",
            "sfi_b",
            "sfi_c",
            "sfi_d",
            "sfi_unspecified",
            "language_test",
        )
    ):
        return "formal"
    if row["strong_qualitative_requirement"]:
        return "strong"
    if row["functional_requirement"] or row["generic_requirement"]:
        return "functional_generic"
    if any(h["status"] in ("preferred", "training_support") for h in hits):
        return "preferred_support"
    return "rejected_candidate" if row["swedish_any_hit"] else "no_requirement"


def review_row(row, **sampling):
    hits = json.loads(row["language_hits_json"])
    return {
        "record_id": row["record_id"],
        "ad_id": row["ad_id"],
        "text_hash": row["text_hash"],
        "year": row["year"],
        "period": period(row["year"]),
        "municipality_id": row["municipality_id"],
        "municipality": row["municipality_name"],
        "job_title": row["original_job_title"],
        "occupation_code": row["occupation_code"],
        "employment_duration": row["employment_duration"],
        "employer_match_method": row["employer_match_method"],
        "primary_eligible": row["primary_eligible"],
        "full_relevant_context": row["description_text"],
        "machine_category": "|".join(sorted({h["category"] + ":" + h["status"] for h in hits})),
        "machine_required": row["swedish_requirement"],
        "machine_formal": row["formal_threshold"],
        "manual_category": "",
        "manual_required": "",
        "manual_formal": "",
        "notes": "",
        "source_url": row["source_url"],
        **sampling,
    }


def validation_samples(records, quotas, seed=20260918, false_negative_size=300):
    population = [
        r
        for r in records
        if (
            r["primary_eligible"]
            or (r["sensitivity_eligible"] and r["elderly_care_context"] == "yes")
        )
        and not r.get("duplicate_ad_id")
    ]
    volume = Counter(r["municipality_id"] for r in population)
    cells = defaultdict(list)
    for row in population:
        # Recruitment volume is a sampling dimension, explicitly not population size.
        key = (
            stratum(row),
            period(row["year"]),
            row["occupation_code"],
            "high_ad_volume" if volume[row["municipality_id"]] >= 500 else "low_ad_volume",
            "permanent"
            if "vidare" in (row["employment_duration"] or "").casefold()
            else "temporary_or_unknown",
        )
        cells[key].append(row)
    rng = random.Random(seed)
    selected, inventory = [], []
    available = Counter(
        {main: sum(len(v) for k, v in cells.items() if k[0] == main) for main in quotas}
    )
    effective = {k: min(n, available[k]) for k, n in quotas.items()}
    remaining = min(sum(quotas.values()), sum(available.values())) - sum(effective.values())
    while remaining:
        for key in sorted(quotas):
            if effective[key] < available[key] and remaining:
                effective[key] += 1
                remaining -= 1
    for main, quota in effective.items():
        keys = sorted(k for k in cells if k[0] == main)
        rng.shuffle(keys)
        allocations = dict.fromkeys(keys, 0)
        for _ in range(min(quota, sum(len(cells[k]) for k in keys))):
            possible = [k for k in keys if allocations[k] < len(cells[k])]
            key = min(possible, key=lambda k: allocations[k])
            allocations[key] += 1
        for key in keys:
            candidates = sorted(cells[key], key=lambda r: r["record_id"])
            count = allocations[key]
            selected.extend(
                review_row(
                    r,
                    sample_stratum=main,
                    sampling_cell="|".join(key),
                    selection_probability=count / len(candidates),
                    sampling_weight=len(candidates) / count,
                )
                for r in rng.sample(candidates, count)
            )
            inventory.append(
                {
                    "sample_stratum": main,
                    "sampling_cell": "|".join(key),
                    "population": len(candidates),
                    "selected": count,
                }
            )
        inventory.append(
            {
                "sample_stratum": main,
                "sampling_cell": "TOTAL",
                "population": sum(len(cells[k]) for k in keys),
                "selected": sum(allocations.values()),
                "requested": quotas[main],
                "effective_quota": quota,
            }
        )
    negatives = sorted(
        (r for r in population if r["primary_eligible"] and not r["swedish_requirement"]),
        key=lambda r: r["record_id"],
    )
    count = min(false_negative_size, len(negatives))
    fn = [
        review_row(
            r,
            sample_stratum="random_predicted_negative",
            sampling_cell="all_predicted_negative",
            selection_probability=count / len(negatives),
            sampling_weight=len(negatives) / count,
        )
        for r in random.Random(seed + 1).sample(negatives, count)
    ]
    columns = "record_id ad_id text_hash year period municipality_id municipality job_title occupation_code employment_duration employer_match_method primary_eligible full_relevant_context machine_category machine_required machine_formal manual_category manual_required manual_formal notes source_url sample_stratum sampling_cell selection_probability sampling_weight".split()
    return (
        pd.DataFrame(selected, columns=columns),
        pd.DataFrame(fn, columns=columns),
        pd.DataFrame(inventory),
    )


def preserve_review(path, frame):
    path = Path(path)
    if path.exists():
        existing = pd.read_csv(path, dtype=str, keep_default_na=False)
        old_keys = set(zip(existing["record_id"], existing["text_hash"], strict=True))
        new_keys = (
            set(zip(frame["record_id"], frame["text_hash"], strict=True)) if len(frame) else set()
        )
        if old_keys != new_keys:
            raise ValueError(
                f"Review sample changed: {path}. Keep existing human coding and use a new review directory."
            )
        return existing  # Never rewrite an existing human coding file.
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return frame


def _bool(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    key = str(value).casefold().strip()
    if key in ("true", "1", "yes", "ja"):
        return True
    if key in ("false", "0", "no", "nej"):
        return False
    raise ValueError(f"Invalid manual binary label: {value!r}")


def metrics(frame):
    results = []
    if frame.empty:
        return pd.DataFrame([{"status": "manual_validation_pending", "n_reviewed": 0}])
    groups = [
        ("all_reviewed_populations", frame),
        *[(p, g) for p, g in frame.groupby("period", sort=True)],
    ]
    if "employer_match_method" in frame:
        groups.extend(
            ("employer_" + str(p), g) for p, g in frame.groupby("employer_match_method", sort=True)
        )
    for label, group in groups:
        for outcome in ("required", "formal"):
            for weighting in ("unweighted_review_sample", "design_weighted"):
                counts = {"TP": 0.0, "FP": 0.0, "FN": 0.0, "TN": 0.0}
                reviewed = 0
                for row in group.to_dict("records"):
                    truth = _bool(row["manual_" + outcome])
                    if truth is None:
                        continue
                    predicted = _bool(row["machine_" + outcome])
                    if predicted is None:
                        raise ValueError("Machine label is missing")
                    weight = (
                        float(row.get("sampling_weight", 1))
                        if weighting == "design_weighted"
                        else 1
                    )
                    counts[("T" if predicted == truth else "F") + ("P" if predicted else "N")] += (
                        weight
                    )
                    reviewed += 1
                tp, fp, fn, tn = (counts[k] for k in ("TP", "FP", "FN", "TN"))
                ppv = tp / (tp + fp) if tp + fp else None
                results.append(
                    {
                        "period": label,
                        "outcome": outcome,
                        "weighting": weighting,
                        "status": "observed_partial_reviews"
                        if reviewed
                        else "manual_validation_pending",
                        "n_reviewed": reviewed,
                        "n_sampled": len(group),
                        **counts,
                        "precision_ppv": ppv,
                        "recall_sensitivity": tp / (tp + fn) if tp + fn else None,
                        "specificity": tn / (tn + fp) if tn + fp else None,
                        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                        "precision_target": 0.95,
                        "observed_precision_at_least_target": ppv >= 0.95
                        if ppv is not None
                        else None,
                    }
                )
    return pd.DataFrame(results)


def false_negative_metrics(frame):
    truths = [_bool(v) for v in frame.get("manual_required", [])]
    reviewed = [v for v in truths if v is not None]
    return pd.DataFrame(
        [
            {
                "n_sampled": len(frame),
                "n_reviewed": len(reviewed),
                "missed_requirements": sum(reviewed),
                "share_missed_among_predicted_negatives": sum(reviewed) / len(reviewed)
                if reviewed
                else None,
                "interpretation": "False omission rate (1-NPV); not FN/(TP+FN). Read complete text.",
                "status": "observed_partial_reviews" if reviewed else "manual_validation_pending",
            }
        ]
    )


def af_comparison(records):
    rows = [r for r in records if r["primary_eligible"] and not r.get("duplicate_ad_id")]
    counts = Counter((r["swedish_requirement"], r["af_must_have_swedish"]) for r in rows)
    table = pd.DataFrame(
        [
            {"our_required_swedish": ours, "af_must_have_swedish": af, "n_ads": counts[(ours, af)]}
            for ours in (False, True)
            for af in (False, True, None)
        ]
    )
    discordant = [
        review_row(r)
        for r in rows
        if r["af_must_have_swedish"] is not None
        and r["swedish_requirement"] != r["af_must_have_swedish"]
    ]
    return table, pd.DataFrame(discordant)


def import_reviews(workbook, review_csv, sheet="Validation"):
    """Import edited workbook labels with identity checking and conflict refusal."""
    from openpyxl import load_workbook

    existing = pd.read_csv(review_csv, dtype=str, keep_default_na=False)
    wb = load_workbook(workbook, read_only=True, data_only=True)
    rows = list(wb[sheet].values)
    wb.close()
    if not rows:
        raise ValueError(f"Empty review sheet: {sheet}")
    if len({r[0] for r in rows[1:] if r[0]}) != sum(bool(r[0]) for r in rows[1:]):
        raise ValueError(f"Duplicate review record IDs: {sheet}")
    incoming = {r[0]: dict(zip(rows[0], r, strict=True)) for r in rows[1:] if r[0]}
    fields = ("manual_category", "manual_required", "manual_formal", "notes")
    for i, row in existing.iterrows():
        candidate = incoming.get(row["record_id"])
        if not candidate:
            continue
        if candidate["text_hash"] != row["text_hash"]:
            raise ValueError("Review text identity changed")
        for field in fields:
            if field not in existing.columns:
                continue
            value = "" if candidate.get(field) is None else str(candidate[field])
            if field in ("manual_required", "manual_formal"):
                _bool(value)
            if row[field] and value and row[field].casefold() != value.casefold():
                raise ValueError(f"Conflicting existing human label for {row['ad_id']} {field}")
            if value:
                existing.at[i, field] = value
    backup = Path(review_csv).with_suffix(
        ".before-import-" + hashlib.sha256(Path(review_csv).read_bytes()).hexdigest()[:12] + ".csv"
    )
    if not backup.exists():
        backup.write_bytes(Path(review_csv).read_bytes())
    existing.to_csv(review_csv, index=False, encoding="utf-8-sig")
