# Swedish requirements in municipal elderly-care recruitment

This separate pipeline measures **recruitment wording** in official Platsbanken advertisements. It imports no municipal-policy modules and creates no policy adoption/status variables. An advertisement can never establish a formal municipal language policy. Future policy linkage is a separate municipality_id/year join, after selecting occupation_group=`overall` or an explicitly chosen occupation.

## Install and run the pilot

Python 3.11–3.13. Run commands from the repository root in an isolated environment:

```sh
python -m venv .venv
# Activate .venv for your platform.
python -m pip install -r requirements.lock.txt -r requirements-recruitment.lock.txt
python -m pip install --no-deps -e .
python scripts/download_historical_ads.py --years 2017 2021 2024
python scripts/classify_historical_ads.py --output research/recruitment_ads/output
python scripts/validate_language_classifier.py --output research/recruitment_ads/output
python scripts/build_municipality_year_panel.py --output research/recruitment_ads/output
```

Occupation filtering happens during streaming classification. The config selects the occupations, pilot municipalities, years, repost window, fixed weights and validation seed. Add configured occupation groups only with corresponding context and terminology validation. The default pilot covers Stockholm, Göteborg, Malmö, Uppsala, Linköping, Gotland, Falun, Umeå, Kiruna and Sorsele. This purposive pilot includes metropolitan, regional and small/rural employers, but **does not estimate national prevalence**.

Use `--sample` with the download script for the official 1% development files in a **different cache directory**. These files have explicit sample flags and do not enter complete-year trend tables. The actual pilot uses complete annual files, not the 1% sample.

## Sources and the employer master

* Annual original/full-text enriched files: https://data.jobtechdev.se/annonser/historiska/berikade/kompletta/
* General archive, including the older files: https://data.jobtechdev.se/annonser/historiska/
* Official file-format introduction: https://gitlab.com/arbetsformedlingen/job-ads/getting-started-code-examples/jupyter-notebook-on-historical-ads
* SCB municipality names/codes: https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/
* Skolverket municipal legal entities: https://api.skolverket.se/skolenhetsregistret/export/skolenhet/adressfil
* Register description: https://www.skolverket.se/styrning-och-ansvar/skolenhetsregistret

`employer_master.csv` contains all 290 municipalities. The source register's **legal form Kommuner** and legal-entity name identify the employer, not the location of its schools. SCB supplies the municipality code/name join. Falu kommun is the explicitly handled legal name for SCB Falun. Every organisation number passes a checksum and the mapping is unique. `employer_master.sources.json` records hashes and retrieval time. The original source snapshots are cached locally, never silently replaced.

To refresh, download the SCB page and the Skolverket workbook to a new cache directory, then run:

```sh
python scripts/build_municipal_employers.py --scb-html path/to/scb.html --register-xlsx path/to/register.xlsx --output path/to/new/employer_master.csv
```

`valid_from` and `valid_to` are blank where historical endpoints are unknown; they are **not invented founding dates**. Historical matching with this current register assumes entity continuity and records `current_snapshot_historical_continuity_unverified`. If historical evidence changes an identifier/name, add non-overlapping dated rows. Heby's pre-2007 code and other historical nomenclature need explicit treatment before a 2006 extension.

Primary identification is organisation-number exact matching only. An existing unrecognised/malformed number cannot be overridden by a name. Exact normalized legal names from the official register are separately validated name aliases. Department names, abbreviations and brand variants require an explicit reviewed alias row before becoming fallback matches. Unresolved employer-name candidates beginning with a pilot municipality's name and `kommun/stad` are retained for audit; their `municipality_id` stays missing, and `candidate_employer_municipality_id` is only a review-routing field. Workplace geography never identifies an employer. Private providers, agencies, contractors and municipal companies with different legal entities are excluded from the primary dataset.

## Population and measures

`occupation_group.legacy_ams_taxonomy_id` is the SSYK group; `occupation.legacy_ams_taxonomy_id` is a different job-title code. Both original structures are retained as JSON, and dictionary/list representations are supported. SSYK 5321 is the core context. SSYK 5330 needs positive job-related elderly-care context. Conflicting occupational titles and multi-group ads go to review. Elderly context `mixed`, `no` or `uncertain` never enters the primary denominator.

Language detection searches full **original description text**, retains exact offsets, phrases, sentences, previous sentence and next two sentences, and stores normalized text separately. Each hit has its own category, semantic status and rule identifier. Formal categories are never translated into one another. Category flags without `_hit` mean **required** evidence; `_hit` flags mean a detected category regardless of semantic status. Multi-category hits and category shares can overlap. `sfi_level` can contain multiple explicitly required levels separated by `|`.

`language_hits.parquet` is the complete one-row-per-hit audit; the ad-level evidence columns contain JSON arrays. Original structured AF language fields, null/missingness and the independent text-derived outcomes are preserved. A missing AF language field is unknown, not an AF-negative classification.

Unclear statements remain `uncertain`. This version does not use an LLM, makes no model calls, and refuses `llm_enabled: true`; therefore there is no unlogged model inference. A later LLM stage must be opt-in, snippet-only, structured and cached with model, prompt version, input hash, timestamp and retry history. Do not enable it simply by changing the flag.

Raw-ad estimates count eligible advertisements. Repeated ad IDs are flagged, retained, and counted only once. Possible recruitment spells use exact employer/municipality/SSYK/normalized title and token-trigram Jaccard similarity within a bounded 45-day window from the first ad. The earliest eligible ad represents each spell. Ads with changed requirement classifications within a spell are flagged. A separate 30-day sensitivity is exported. These are possible spells, not known individual hiring events.

Vacancy weighting uses known positive integers and reports missing counts. Missing-as-one appears only as an explicitly named sensitivity. `share_no_language_requirement` means no detected required statement, not proven absence; `share_uncertain` accompanies it. National/pooled tables use ad-level counts, never an unweighted average of municipality percentages. Fixed equal occupation weights (0.5/0.5, configurable) avoid composition changes; an absent occupation denominator gives a missing standardized rate.

## Validation and human coding

```sh
python scripts/validate_language_classifier.py
# Edit manual_category, manual_required, manual_formal, notes in validation.csv.
# Also code complete text in the separate false_negatives.csv.
# Alternatively edit validation.xlsx, then import both review sheets:
python scripts/validate_language_classifier.py --import-workbook path/to/reviewed-validation.xlsx
# Score saved labels and update tables without replacing the edited workbook:
python scripts/validate_language_classifier.py
python scripts/build_municipality_year_panel.py --skip-review-workbook
```

The roughly 600-ad sample uses mutually exclusive category strata, then balances period, occupation, recruitment-volume band and employment duration. Recruitment volume is not population size. Rare strata are exhausted and their unfilled quotas deterministically redistributed; the sampling inventory records target and effective allocations and each cell's inclusion probability. The sample includes clearly labelled name-fallback candidates so 2017 terminology can be reviewed. Overall, period-specific and employer-method metrics are separate. The extra 300 predicted-negative ads are a seeded random sample from the **primary** population and may overlap the stratified sample. They are a separate review exercise, not pooled with the stratified metrics.

The development inspection in `validation/development_review.json` records three ad IDs used to investigate rule failures. It is agent-assisted rule development, not human gold coding; flag those examples if a later labelled test set contains them.

No gold labels are supplied for real ads. Missing human labels leave precision, recall, specificity and F1 missing. Confusion matrices are observed only after coding. Both unweighted review-sample and design-weighted metrics are exported; reviewing only convenient cases invalidates population interpretations. The 95% PPV target is never asserted as achieved. The negative-only sample estimates the **false omission rate**, FN/(FN+TN), not FN/(TP+FN). True classifier sensitivity comes from the broader labelled sample. Review date/annotator and adjudication procedures can be recorded in notes.

Regenerating a sample never overwrites an existing human coding CSV; differing sample identities require a new output directory. Workbook import matches record ID and original-text hash, preserves previous coding, rejects conflicts and creates a content-addressed backup. Keep workbook and CSV reviews synchronized before exporting. Rule revisions must use a new version/output directory and be evaluated on a held-out reviewed sample; training examples alone are not evidence of accuracy.

## Outputs and coverage

The output directory contains `ads_classified.parquet`, `recruitment_spells.parquet`, `language_hits.parquet`, `municipality_year.parquet`, `municipality_year_spells.parquet`, review CSVs, observed metric CSVs, per-source inventories, config/code/source hashes, a separate analysis-module manifest, `validation.xlsx`, `recruitment_language_requirements.xlsx`, machine-readable summary tables and six figures (PNG and SVG).

The analytical workbook includes Municipality_Year, National_Trends, Occupation_Trends, Language_Categories, Validation, False_Negatives, AF_Comparison, Coverage, Employer_Matches, Methodology, Audit, Spell_Trends and Sensitivity. During a pilot, even the requested National_Trends sheet is explicitly scoped to the pilot municipalities. Figures show pilot aggregates only. No 2016–2025 national trend is claimed.

Coverage is GOOD >=20, MODERATE 5–19, SPARSE 1–4, NO_ADS 0. A zero observed denominator yields **missing shares**, not 0%. Unprocessed years have missing counts/shares and NOT_PROCESSED. An entire archive without employer organisation numbers yields EMPLOYER_ID_GAP for primary results. All municipality/year/occupation combinations in the selected scope are retained.

The 2021 annual file includes records published outside its nominal year; these are counted in `publication_year_mismatch_or_missing` and excluded from that annual partition. This does not prove that the appropriate annual archive contains them. Source coverage is bounded by Platsbanken, the supplied archive and employer matching. Calendar-complete file labels never assert exhaustive recruitment coverage. See `SCHEMA_COMPARABILITY.md` and `PILOT_REPORT.md`.

## Restartability, expansion and checks

Raw downloads have SHA-256 hashes, URLs, original filenames, timestamps, ETag/Last-Modified and explicit source versions. Existing cached files are verified before use; changed inputs are refused. Interrupted partial downloads restart from byte zero. Classification checkpoints resume completed years only when input/config/code fingerprints and checkpoint hashes match. An interrupted year is scanned again. Changing rules or scope requires a new output directory.

After pilot human coding and a documented decision on the early employer-ID gap, full primary processing is explicitly requested with:

```sh
python scripts/download_historical_ads.py --years 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025
python scripts/classify_historical_ads.py --full --output research/recruitment_ads/output-full-v1
python scripts/validate_language_classifier.py --output research/recruitment_ads/output-full-v1
python scripts/build_municipality_year_panel.py --output research/recruitment_ads/output-full-v1
```

The first execution intentionally stops at the pilot/review checkpoint. 2006–2015 has no approved comparable adapter. 2026 quarterly data is not downloaded by the annual command; a separately scoped partial-year adapter is still needed. Both extension periods are excluded by default.

```sh
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m pytest tests/test_recruitment_ads.py -q
```

The supplied pilot ad classifications use the frozen classification code recorded in `run.json`. Table/figure exports record their own module hashes in `analysis_manifest.json`; later presentation fixes need not be mistaken for new classification evidence. A changed checkout can intentionally require a fresh output directory even when only export code changed.
