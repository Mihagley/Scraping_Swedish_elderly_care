import json
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
import yaml

from recruitment_ads.aggregate import build_panel, measures, national_trends
from recruitment_ads.classify import classify_ad
from recruitment_ads.deduplicate import assign_spells
from recruitment_ads.download import iter_ads, sha256, write_json
from recruitment_ads.elderly_care import context
from recruitment_ads.employer import EmployerMaster, normalize_orgnr
from recruitment_ads.export import prepare_tables
from recruitment_ads.language_rules import classify_language
from recruitment_ads.occupations import occupation
from recruitment_ads.pipeline import run_classification, save_ads
from recruitment_ads.schemas import SCHEMA
from recruitment_ads.validate import (
    false_negative_metrics,
    metrics,
    preserve_review,
    validation_samples,
)

ROOT = Path(__file__).parents[1]
CONFIG = yaml.safe_load((ROOT / "research/recruitment_ads/config.yaml").read_text(encoding="utf-8"))
MASTER = EmployerMaster.load(
    ROOT / "research/recruitment_ads/employer_master.csv",
    ROOT / "research/recruitment_ads/employer_aliases.csv",
)
SOURCE = {
    "source_hash": "synthetic",
    "source_file": "fixture.jsonl",
    "source_url": "https://data.jobtechdev.se/fixture",
    "retrieved_at": "2026-09-18T00:00:00Z",
    "data_version": "synthetic",
    "source_year": 2024,
    "is_sample": False,
    "calendar_complete": True,
}


def raw(text="För tjänsten krävs goda kunskaper i svenska.", **kwargs):
    return {
        "id": "a",
        "publication_date": "2024-01-01T10:00:00",
        "employer": {"name": "Stockholms kommun", "organization_number": "212000-0142"},
        "occupation_group": {"legacy_ams_taxonomy_id": "5321", "label": "Undersköterskor"},
        "headline": "Undersköterska till äldreboende",
        "description": {"text": text},
        "number_of_vacancies": 2,
        **kwargs,
    }


def ad(text="För tjänsten krävs goda kunskaper i svenska.", **kwargs):
    return classify_ad(raw(text, **kwargs), SOURCE, MASTER, CONFIG)


@pytest.mark.parametrize(
    "text,category,status",
    [
        ("För tjänsten krävs goda kunskaper i svenska.", "generic_swedish_requirement", "required"),
        (
            "Du behöver kunna uttrycka dig väl på svenska i tal och skrift.",
            "functional_oral_written",
            "required",
        ),
        ("Du ska ha godkänt betyg i Svenska 1.", "svenska_1", "required"),
        ("Du ska ha svenska som andraspråk 1.", "svenska_som_andrasprak_1", "required"),
        ("Du ska ha SVA 1.", "sva_1", "required"),
        ("Minst Svenska A krävs.", "svenska_a", "required"),
        ("Du ska ha SAS A.", "svenska_som_andrasprak_a", "required"),
        ("Du ska ha Svenska som andraspråk A.", "svenska_som_andrasprak_a", "required"),
        ("Du ska ha svenska på GERS B1.", "gers_b1", "required"),
        ("Svenska på CEFR B2 krävs.", "gers_b2", "required"),
        ("Du ska ha svenska på C1.", "other_cefr_level", "required"),
        *[
            (f"Du ska ha godkänt SFI {level}.", "sfi_" + level.lower(), "required")
            for level in "ABCD"
        ],
        ("Du måste genomföra ett språktest i svenska.", "language_test", "required"),
        ("Du måste göra ett språktest i engelska.", "language_test", "irrelevant"),
        ("Du ska genomföra språktest.", "language_test", "uncertain"),
        ("Du har mycket goda kunskaper i det svenska språket.", "strong_qualitative", "required"),
        ("Du ska ha fullgod svenska.", "strong_qualitative", "required"),
        ("För arbetet behöver du behärska svenska språket.", "strong_qualitative", "required"),
        ("Du bör behärska svenska.", "strong_qualitative", "preferred"),
        ("Kunskaper i svenska är meriterande.", "generic_swedish_requirement", "preferred"),
        ("Det är en fördel om du behärskar svenska.", "strong_qualitative", "preferred"),
        ("Vi erbjuder svenskundervisning.", "untyped", "training_support"),
        ("Du får språkstöd på arbetsplatsen.", "untyped", "training_support"),
        ("Språkutvecklande arbetsplats.", "untyped", "training_support"),
        ("Du kommer att dokumentera på svenska.", "untyped", "descriptive"),
        ("Arbetsplatsen består av svensk- och finsktalande personal.", "untyped", "descriptive"),
        (
            "Du behöver behärska svenska eller engelska.",
            "strong_qualitative",
            "alternative_language",
        ),
        ("Ansökan ska vara på svenska.", "untyped", "application_instruction"),
        ("Ansökan kan lämnas på svenska eller engelska.", "untyped", "application_instruction"),
        ("SFI-studerande är välkomna att söka.", "sfi_unspecified", "training_support"),
        ("Vi erbjuder SFI-utbildning.", "sfi_unspecified", "training_support"),
        ("Du kan kombinera arbetet med SFI.", "sfi_unspecified", "training_support"),
        ("Kunskaper i svenska krävs inte.", "generic_swedish_requirement", "irrelevant"),
        ("Du ska ha svensk undersköterskeutbildning.", "untyped", "irrelevant"),
        ("Engelska på B2 krävs.", "gers_b2", "irrelevant"),
        ("Du ska ha B1 körkort och kunna svenska.", "gers_b1", "uncertain"),
    ],
)
def test_statements(text, category, status):
    hits = classify_language(text)
    assert any(h["category"] == category and h["status"] == status for h in hits), hits
    assert all(text[h["start"] : h["end"]] == h["matched_phrase"] for h in hits)


def test_clause_scope_and_equivalence():
    result = ad("Svenska krävs, engelska är meriterande. Du ska ha Svenska 1 eller SFI D.")
    assert result["swedish_requirement"] and result["svenska_1"] and result["sfi_d"]
    assert not result["gers_b1"] and not result["gers_b2"]
    assert not ad("Du behöver behärska svenska eller engelska.")["swedish_requirement"]
    assert ad("Du behöver behärska svenska eller engelska.")["alternative_language_accepted"]
    assert ad("Krav\n• Utbildad undersköterska\n• Svenska i tal och skrift")["swedish_requirement"]


def test_employer_master_and_fallback():
    assert len(MASTER.rows) == 290
    assert len({r["municipality_orgnr"] for r in MASTER.rows}) == 290
    assert normalize_orgnr("16212000-0142") == "2120000142"
    assert normalize_orgnr("2120000143") is None
    assert (
        MASTER.match("2120000142", "Private agency", "2024-01-01")["employer_match_method"]
        == "orgnr_exact"
    )
    assert (
        MASTER.match(None, "Stockholms kommun", "2024-01-01")["employer_match_method"]
        == "employer_name_validated"
    )
    assert (
        MASTER.match("5560000000", "Stockholms kommun", "2024-01-01")["employer_match_method"]
        == "unresolved"
    )
    assert not ad(
        employer={"name": "Staffing AB", "organization_number": "5560000000"},
        workplace_address={"municipality": "Stockholm"},
    )["primary_eligible"]
    assert not ad(employer={"name": "Stockholms kommun", "organization_number": None})[
        "primary_eligible"
    ]


def test_temporal_matching_and_ambiguity():
    row = {**MASTER.rows[0], "valid_from": "2020-01-01", "valid_to": "2021-12-31"}
    m = EmployerMaster([row])
    assert m.match(row["municipality_orgnr"], "", "2019-01-01")["municipality_id"] is None
    assert (
        m.match(row["municipality_orgnr"], "", "2020-01-01")["municipality_id"]
        == row["municipality_id"]
    )
    with pytest.raises(ValueError, match="Overlapping"):
        EmployerMaster([row, row])


def test_occupations_and_context():
    assert ad()["primary_eligible"]
    assert (
        occupation(
            raw(
                occupation={"legacy_ams_taxonomy_id": "5330"},
                occupation_group={"legacy_ams_taxonomy_id": "2221"},
            )
        )["occupation_candidate"]
        is False
    )
    group = {"legacy_ams_taxonomy_id": "5330", "label": "Vårdbiträden"}
    assert ad("Du arbetar med omsorg om äldre.", occupation_group=[group], headline="Vårdbiträde")[
        "primary_eligible"
    ]
    assert not ad(
        "Du arbetar med personlig assistans inom LSS.",
        occupation_group=group,
        headline="Vårdbiträde",
    )["primary_eligible"]
    assert (
        context("Vårdbiträde", "Du måste vara 18 år eller äldre.", "5330")["elderly_care_context"]
        == "uncertain"
    )
    assert context("Hemtjänst och LSS", "", "5330")["elderly_care_context"] == "mixed"
    assert not ad(headline="Sjuksköterska på äldreboende")["primary_eligible"]
    assert not ad(occupation_group=None)["primary_eligible"]


def test_original_text_af_missing_and_schema(tmp_path):
    text = "  Du SKA ha Svenska 1.\nVi erbjuder språkstöd."
    row = ad(text, must_have={"languages": [{"label": "svenska"}]}, nice_to_have={"languages": []})
    assert row["description_text"] == text and row["normalized_text"] != text
    assert row["af_must_have_swedish"] is True and row["af_nice_to_have_swedish"] is False
    assert ad()["af_must_have_swedish"] is None
    path = tmp_path / "ads.parquet"
    save_ads(path, [row])
    assert pq.read_schema(path) == SCHEMA
    save_ads(path, [])
    assert pq.read_schema(path) == SCHEMA


def test_spells_anchor_window_and_preservation():
    records = [
        ad(id=str(i), publication_date=d + "T00:00:00")
        for i, d in enumerate(("2024-01-01", "2024-02-01", "2024-03-01"))
    ]
    rows, spells = assign_spells(records, 45)
    assert len(rows) == 3 and len(spells) == 2
    assert rows[0]["recruitment_spell_id"] == rows[1]["recruitment_spell_id"]
    rows, _ = assign_spells(records, 30)
    assert rows[0]["recruitment_spell_id"] != rows[1]["recruitment_spell_id"]
    duplicates, _ = assign_spells([records[0], records[0]])
    assert sum(r["primary_eligible"] for r in duplicates) == 1


def test_denominators_no_ads_unprocessed_vacancy_weights():
    rows, _ = assign_spells(
        [
            ad(id="yes", number_of_vacancies=4),
            ad("Välkommen till oss.", id="no", number_of_vacancies=1),
            ad(id="missing", number_of_vacancies=None),
        ]
    )
    values = measures(rows)
    assert values["share_required_ads"] == pytest.approx(2 / 3)
    assert values["share_required_vacancies"] == pytest.approx(4 / 5)
    assert values["share_required_vacancies_missing_as_one"] == pytest.approx(5 / 6)
    municipalities = [next(r for r in MASTER.rows if r["municipality_id"] == "0180")]
    panel = build_panel(
        rows,
        municipalities,
        [2023, 2024, 2025, 2026],
        {2023: SOURCE, 2024: SOURCE, 2026: {**SOURCE, "calendar_complete": False}},
    )
    no_ads = panel[(panel.year == 2023) & (panel.occupation_group == "overall")].iloc[0]
    assert no_ads.coverage_flag == "NO_ADS" and pd.isna(no_ads.share_required_ads)
    missing = panel[panel.year == 2025].iloc[0]
    assert missing.coverage_flag == "NOT_PROCESSED" and pd.isna(missing.n_ads)
    assert not panel[panel.year == 2026].complete_year_trend_eligible.any()
    gap = build_panel(
        rows, municipalities, [2017], {2017: {**SOURCE, "employer_identifier_gap": True}}
    )
    assert gap.n_ads.isna().all() and gap.share_required_ads.isna().all()
    national, _ = national_trends(
        rows,
        {2024: SOURCE, 2026: {**SOURCE, "calendar_complete": False}},
        {"5321": 0.5, "5330": 0.5},
        "pilot",
    )
    assert len(national) == 1 and pd.isna(national.iloc[0].standardised_share_required)


def test_validation_reproducibility_preserves_coding_and_metrics(tmp_path):
    records, _ = assign_spells(
        [
            ad("Vi erbjuder SFI-utbildning." if i % 2 else "Du ska ha Svenska 1.", id=str(i))
            for i in range(50)
        ]
    )
    a, fn, inventory = validation_samples(records, CONFIG["validation_quotas"], seed=10)
    b, _, _ = validation_samples(list(reversed(records)), CONFIG["validation_quotas"], seed=10)
    assert list(a.record_id) == list(b.record_id)
    assert len(fn) == 25
    assert not inventory.empty
    assert metrics(a).n_reviewed.eq(0).all()
    assert pd.isna(false_negative_metrics(fn).iloc[0].share_missed_among_predicted_negatives)
    path = tmp_path / "validation.csv"
    preserve_review(path, a)
    a.loc[0, "manual_required"] = "true"
    a.loc[0, "notes"] = "Human adjudication"
    a.to_csv(path, index=False)
    before = path.read_bytes()
    preserve_review(path, b)
    assert path.read_bytes() == before
    fixture = pd.DataFrame(
        [
            {
                "period": "2016–2018",
                "machine_required": p,
                "manual_required": t,
                "machine_formal": p,
                "manual_formal": t,
                "sampling_weight": 1,
            }
            for p, t in [(True, True), (True, False), (False, True), (False, False)]
        ]
    )
    m = metrics(fixture).iloc[0]
    assert m.precision_ppv == m.recall_sensitivity == m.specificity == m.f1 == 0.5
    assert (m.TP, m.FP, m.FN, m.TN) == (1, 1, 1, 1)


def test_small_end_to_end_and_resume(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                **CONFIG,
                "pilot_years": [2024],
                "years": [2023, 2024],
                "pilot_municipalities": ["0180"],
            }
        ),
        encoding="utf-8",
    )
    path = cache / "2024.jsonl.zip"
    fixture = [
        raw(id="1"),
        raw("Vi erbjuder SFI-utbildning.", id="2", number_of_vacancies=None),
        raw(
            "Du ska ha svenska på B2.", id="3", occupation_group={"legacy_ams_taxonomy_id": "5330"}
        ),
    ]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("2024.jsonl", "\n".join(json.dumps(r, ensure_ascii=False) for r in fixture))
    assert len(list(iter_ads(path))) == 3
    write_json(
        path.with_suffix(".zip.manifest.json"),
        {**SOURCE, "source_file": path.name, "source_hash": sha256(path)},
    )
    output = tmp_path / "output"
    arguments = (
        config_path,
        ROOT / "research/recruitment_ads/employer_master.csv",
        ROOT / "research/recruitment_ads/employer_aliases.csv",
        cache,
        output,
        [2024],
        ["0180"],
    )
    ads, spells = run_classification(*arguments)
    assert len(ads) == 3 and len(spells) == 3
    before = (output / "checkpoints/2024.parquet").stat().st_mtime_ns
    run_classification(*arguments)
    assert (output / "checkpoints/2024.parquet").stat().st_mtime_ns == before
    sample, negatives, _ = validation_samples(ads, CONFIG["validation_quotas"])
    preserve_review(output / "validation.csv", sample)
    preserve_review(output / "false_negatives.csv", negatives)
    review_before = (output / "validation.csv").read_bytes()
    negatives_before = (output / "false_negatives.csv").read_bytes()
    tables, reviews = prepare_tables(
        output, ROOT / "research/recruitment_ads/employer_master.csv", config_path
    )
    assert {"Municipality_Year", "Validation", "Coverage", "Audit"} <= set(tables)
    assert len(reviews["Validation"]) == 3
    assert (output / "validation.csv").read_bytes() == review_before
    assert (output / "false_negatives.csv").read_bytes() == negatives_before
    prepare_tables(output, ROOT / "research/recruitment_ads/employer_master.csv", config_path)
    assert (output / "validation.csv").read_bytes() == review_before
    assert (output / "false_negatives.csv").read_bytes() == negatives_before
    assert (output / "tables/Validation.csv").exists()
    assert (output / "municipality_year.parquet").exists()
    assert not tables["National_Trends"].all_municipalities_in_scope.any()
    shutil.copyfile(config_path, tmp_path / "config-copy.yaml")
    config_path.write_text(yaml.safe_dump({**CONFIG, "seed": 9}))
    with pytest.raises(ValueError, match="Inputs/code/config changed"):
        run_classification(*arguments)


@pytest.mark.parametrize(
    "text",
    [
        "Krav:\n- Språktest krävs.",
        "Du ska ha svenska referenser.",
        "Du ska ha svenska kollegor.",
    ],
)
def test_ambiguous_candidate_is_not_required(text):
    assert not ad(text)["swedish_requirement"]


def test_checked_in_schema_contract():
    contract = json.loads((ROOT / "research/recruitment_ads/ad_schema.json").read_text())
    assert contract == [
        {"name": f.name, "type": str(f.type), "nullable": f.nullable} for f in SCHEMA
    ]


def test_all_gap_sources_have_stable_empty_trend_schema():
    sources = {2017: {**SOURCE, "employer_identifier_gap": True}}
    overall, occupations = national_trends([], sources, CONFIG["fixed_occupation_weights"], "pilot")
    assert overall.empty and occupations.empty
    assert {"year", "share_required_ads"} <= set(overall.columns)


def test_workbook_import_preserves_both_review_samples(tmp_path):
    from recruitment_ads.export import export_xlsx
    from recruitment_ads.validate import import_reviews

    records, _ = assign_spells([ad("Vi erbjuder SFI-utbildning.")])
    sample, negative, _ = validation_samples(records, CONFIG["validation_quotas"])
    path = tmp_path / "review.csv"
    preserve_review(path, negative)
    negative.loc[0, "manual_required"] = "false"
    negative.loc[0, "notes"] = "Human decision"
    workbook = tmp_path / "review.xlsx"
    export_xlsx({"Validation": sample, "False_Negatives": negative}, workbook)
    import_reviews(workbook, path, "False_Negatives")
    saved = pd.read_csv(path, keep_default_na=False)
    assert saved.loc[0, "notes"] == "Human decision"
    assert len(list(tmp_path.glob("*.before-import-*.csv"))) == 1
    negative.loc[0, "manual_required"] = "true"
    export_xlsx({"False_Negatives": negative}, workbook)
    with pytest.raises(ValueError, match="Conflicting"):
        import_reviews(workbook, path, "False_Negatives")


def test_context_review_candidates_explain_empty_primary_denominator():
    row = ad("Arbeta inom hemtjänst och LSS.")
    panel = build_panel(
        [row],
        [{"municipality_id": "0180", "municipality_name": "Stockholm"}],
        [2024],
        {2024: SOURCE},
    )
    overall = panel[panel.occupation_group == "overall"].iloc[0]
    assert overall.n_ads == 0 and pd.isna(overall.share_required_ads)
    assert overall.n_candidate_ads == 1 and overall.n_context_mixed_candidates == 1


@pytest.mark.parametrize(
    "text", ["Du måste vara svensktalande.", "Du har goda svenskkunskaper i tal och skrift."]
)
def test_compound_swedish_proficiency_wording(text):
    row = ad(text)
    assert row["swedish_requirement"] and row["generic_requirement"]


def test_configured_additional_occupation_is_not_a_core_title_conflict():
    raw = {
        "occupation_group": {"legacy_ams_taxonomy_id": "2221"},
        "headline": "Sjuksköterska till äldreomsorgen",
    }
    assert not occupation(raw)["occupation_eligible"]
    assert occupation(raw, targets=("5321", "5330", "2221"))["occupation_eligible"]
