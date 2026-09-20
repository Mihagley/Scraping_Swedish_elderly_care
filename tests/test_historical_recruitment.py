import json
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
import yaml

from recruitment_ads.classify import classify_ad
from recruitment_ads.download import sha256, write_json
from recruitment_ads.employer import EmployerMaster
from recruitment_ads.historical import LegacyOccupations, classify_historical
from recruitment_ads.historical_pipeline import historical_panel, historical_spells, run_historical
from recruitment_ads.language_rules import classify_language
from recruitment_ads.pipeline import save_ads
from recruitment_ads.validate import period

ROOT = Path(__file__).parents[1]
RESEARCH = ROOT / "research/recruitment_ads"
CONFIG = yaml.safe_load((RESEARCH / "config.yaml").read_text(encoding="utf-8"))
MASTER = EmployerMaster.load(RESEARCH / "employer_master.csv", RESEARCH / "employer_aliases.csv")
MAPPING = [
    {"taxonomy/deprecated-legacy-id": "7586", "taxonomy/ssyk-code-2012": "5321"},
    {"taxonomy/deprecated-legacy-id": "5797", "taxonomy/ssyk-code-2012": "5330"},
    {"taxonomy/deprecated-legacy-id": "other", "taxonomy/ssyk-code-2012": "2221"},
]
CROSSWALK = LegacyOccupations(MAPPING, "https://taxonomy.api.jobtechdev.se/test", "fixture-hash")
SOURCE = {
    "source_hash": "fixture",
    "source_file": "2010.jsonl.zip",
    "source_url": "https://data.jobtechdev.se/annonser/historiska/2010.jsonl.zip",
    "source_year": 2010,
    "data_version": "test",
    "calendar_complete": False,
    "is_sample": False,
}


def raw(code="7586", text="Arbeta i hemtjänsten. Du ska ha goda kunskaper i svenska.", **overrides):
    return {
        "id": None,
        "publication_date": "2010-01-03",
        "headline": "Undersköterska",
        "occupation": {"legacy_ams_taxonomy_id": code},
        "occupation_group": {"legacy_ams_taxonomy_id": None},
        "employer": {"name": "Stockholms kommun", "organization_number": None},
        "description": {"text": text},
        "number_of_vacancies": 2,
        "other_old_legacy_attributes": {"TILLTRADE": "Snarast"},
        **overrides,
    }


def classify(value=None, line=1, crosswalk=CROSSWALK):
    return classify_historical(
        value or raw(), SOURCE, MASTER, CONFIG, crosswalk, "2010.jsonl", line
    )


def test_legacy_mapping_preserves_original_fields_and_identity():
    source = raw()
    result = classify(source)
    assert result["occupation_code"] == "5321"
    assert json.loads(result["original_occupation_group_json"]) == source["occupation_group"]
    assert json.loads(result["original_occupation_json"]) == source["occupation"]
    assert result["description_text"] == source["description"]["text"]
    assert result["occupation_mapping_status"] == "official_legacy_crosswalk"
    assert result["occupation_mapping_hash"] == "fixture-hash"
    assert result["source_ad_id"] is None
    assert result["ad_identity_method"] == "source_record_locator"
    assert result["historical_eligible"] and not result["primary_eligible"]
    assert result["swedish_requirement"]


def test_source_record_locators_are_deterministic_and_distinct():
    assert classify(line=1)["ad_id"] == classify(line=1)["ad_id"]
    assert classify(line=1)["ad_id"] != classify(line=2)["ad_id"]
    assert classify(line=1)["raw_record_hash"] == classify(line=2)["raw_record_hash"]
    official = classify(raw(id="official-123"))
    assert official["ad_id"] == official["source_ad_id"] == "official-123"
    assert official["ad_identity_method"] == "official_ad_id"


def test_unmapped_titles_remain_exploratory_without_imputed_ssyk():
    result = classify(raw(code="5706"))
    assert result["occupation_code"] is None
    assert result["historical_occupation_group"] == "title_underskoterska"
    assert result["historical_cohort"] == "unmapped_title_context_exploratory"


@pytest.mark.parametrize(
    "text",
    ["Du ska kunna svenska.", "Arbeta inom LSS och hemtjänst.", "Arbeta som personlig assistent."],
)
def test_mapped_older_cases_need_explicit_unmixed_elderly_context(text):
    assert not classify(raw(text=text))["historical_eligible"]


def test_private_or_unresolved_employer_cannot_enter_historical_cohort():
    result = classify(
        raw(
            employer={"name": "Privat vård AB", "organization_number": None},
            workplace_address={"municipality_code": "0180"},
        )
    )
    assert not result["historical_eligible"]
    assert result["municipality_id"] is None


def test_ambiguous_mapping_and_structured_conflicts_are_not_title_fallbacks():
    ambiguous = LegacyOccupations(
        MAPPING + [{"taxonomy/deprecated-legacy-id": "7586", "taxonomy/ssyk-code-2012": "5330"}],
        "source",
        "hash",
    )
    assert not classify(crosswalk=ambiguous)["historical_eligible"]
    assert not classify(raw(code="other"))["historical_eligible"]
    assert not classify(
        raw(
            occupation_group=[
                {"legacy_ams_taxonomy_id": "5321"},
                {"legacy_ams_taxonomy_id": "2221"},
            ]
        )
    )["historical_eligible"]
    assert not classify(raw(code="5706", headline="Undersköterska och sjuksköterska"))[
        "historical_eligible"
    ]


def test_historical_spells_do_not_modify_primary_or_impute_occupation_codes():
    a, b = (
        classify(raw(code="5706")),
        classify(raw(code="5706", publication_date="2010-01-10"), line=2),
    )
    records, spells = historical_spells([a, b])
    assert len(spells) == 1 and spells[0]["n_ads_in_spell"] == 2
    assert all(not r["primary_eligible"] and r["occupation_code"] is None for r in records + spells)
    assert all(r["historical_recruitment_spell_id"] for r in records)


def test_historical_panel_distinguishes_zero_ads_unprocessed_and_cohorts():
    records, _ = historical_spells([classify()])
    panel = historical_panel(
        records,
        [{"municipality_id": "0180", "municipality_name": "Stockholm"}],
        [2010, 2011],
        {2010},
    )
    observed = panel[
        (panel.year == 2010)
        & (panel.historical_cohort == "mapped_occupation_sensitivity")
        & (panel.occupation_group == "overall")
    ].iloc[0]
    assert observed.n_ads == 1 and observed.share_required_ads == 1
    assert observed.n_name_validated == 1 and observed.n_orgnr_exact == 0
    assert observed.n_source_record_locators == 1
    no_ads = panel[(panel.year == 2010) & (panel.occupation_group == "5330")].iloc[0]
    assert no_ads.coverage_flag == "NO_ADS" and pd.isna(no_ads.share_required_ads)
    assert panel[panel.year == 2011].n_ads.isna().all()
    assert set(panel[panel.year == 2011].coverage_flag) == {"NOT_PROCESSED"}
    assert period(2006) == "2006–2010" and period(2015) == "2011–2015"


def test_historical_end_to_end_resume_and_review_preservation(tmp_path):
    cache, recent, output = [tmp_path / name for name in ["cache", "recent", "output"]]
    cache.mkdir()
    recent.mkdir()
    archive = cache / "2010.jsonl.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "2010.jsonl",
            "\n".join(
                json.dumps(r)
                for r in [
                    raw(),
                    raw(
                        code="5706",
                        publication_date="2010-02-03",
                        text="Arbeta i hemtjänsten. Vi erbjuder SFI-utbildning.",
                    ),
                ]
            ),
        )
    write_json(
        archive.with_suffix(".zip.manifest.json"), {**SOURCE, "source_hash": sha256(archive)}
    )
    taxonomy = tmp_path / "taxonomy.json"
    write_json(taxonomy, MAPPING)
    recent_source = {
        **SOURCE,
        "source_year": 2017,
        "source_hash": "recent",
        "calendar_complete": True,
    }
    recent_raw = raw(
        id="recent-ad",
        publication_date="2017-01-03",
        occupation_group={"legacy_ams_taxonomy_id": "5321"},
    )
    save_ads(
        recent / "ads_classified.parquet", [classify_ad(recent_raw, recent_source, MASTER, CONFIG)]
    )
    write_json(
        recent / "source_inventory.json",
        [{"year": 2017, "source": recent_source, "counts": {"source_records": 1}}],
    )
    args = (
        RESEARCH / "config.yaml",
        RESEARCH / "employer_master.csv",
        RESEARCH / "employer_aliases.csv",
        taxonomy,
        cache,
        recent,
        output,
        [2010],
    )
    records, spells = run_historical(*args)
    assert len(records) == 3 and len(spells) == 3
    before = sha256(output / "ads_historical.parquet")
    review = pd.read_csv(output / "validation.csv", dtype=str, keep_default_na=False)
    review.loc[0, "manual_required"] = "true"
    review.loc[0, "notes"] = "Human coding must survive regeneration"
    review.to_csv(output / "validation.csv", index=False)
    run_historical(*args)
    assert sha256(output / "ads_historical.parquet") == before
    assert "Human coding" in (output / "validation.csv").read_text()
    panel = pq.read_table(output / "municipality_year_historical.parquet").to_pandas()
    assert panel[panel.year == 2006].n_ads.isna().all()
    assert not json.loads((output / "audit_historical.json").read_text())["combined_primary_trend"]


@pytest.mark.parametrize("separator", ["\r", "\r\n", "\n"])
def test_legacy_line_breaks_keep_evidence_offsets(separator):
    text = separator.join(
        ["Kvalifikationer:", "- Fullgod svenska", "Ansökan", "Skriv din ansökan på svenska."]
    )
    hits = classify_language(text)
    assert any(h["category"] == "strong_qualitative" and h["status"] == "required" for h in hits)
    assert any(h["status"] == "application_instruction" for h in hits)
    assert all(text[h["start"] : h["end"]] == h["matched_phrase"] for h in hits)


@pytest.mark.parametrize("verb", ["utrycka", "uttrycka"])
def test_historical_spelling_in_explicit_requirement(verb):
    hits = classify_language(f"Du måste {verb} dig väl i tal och skrift på svenska.")
    assert any(
        h["category"] == "functional_oral_written" and h["status"] == "required" for h in hits
    )


def test_application_mention_does_not_swallow_proficiency_requirement():
    hits = classify_language(
        "Du ska ha goda kunskaper i svenska och ange ditt körkort i din ansökan."
    )
    assert any(h["status"] == "required" for h in hits)
    assert not any(h["status"] == "application_instruction" for h in hits)


def test_conditional_attachment_of_existing_grades_is_not_a_threshold():
    hits = classify_language(
        "Om du har en tidigare utbildning med godkänt betyg i Svenska 1 ska dessa betyg bifogas din ansökan."
    )
    assert hits and all(h["status"] == "application_instruction" for h in hits)


def test_training_admission_is_not_automatically_a_job_entry_requirement():
    hits = classify_language(
        "Komplettera din ansökan med betyg som visar att du är godkänd i svenska så vi vet att du har rätt behörighet till utbildningen."
    )
    assert hits and all(h["status"] == "uncertain" for h in hits)
