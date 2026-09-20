# Full recruitment-ad execution report

This is the frozen first execution at classifier 1.2. For subsequently recovered 2006–2015 cases and 2016–2020 employer-name sensitivity records, see [HISTORICAL_EXTENSION.md](HISTORICAL_EXTENSION.md). Those outputs use a separate cohort and classifier 1.3.1; this report's counts are unchanged.

Execution: 19 September 2026; exports and checks: 20 September 2026. Classifier: `recruitment-sv-1.2.0`. These are **unvalidated recruitment-wording measures**. No advertisement establishes a formal municipal language policy.

## Completed scope and counts

All ten full official enriched annual archives, 2016–2025, were downloaded, hashed and scanned after the ten-municipality 2017/2021/2024 pilot and rule-development inspection. The full scan read **7,367,297 source records**, retained **74,800 candidate ads**, and identified **26,772 primary-eligible ads** and **24,196 possible primary recruitment spells**. All 290 municipalities are in the employer master and panel. This geographical scope does not establish representative or exhaustive recruitment coverage.

| Year | Source records | Retained | Primary ads | Primary spells | Missing employer orgnr |
| --- | --- | --- | --- | --- | --- |
| 2016 | 718,046 | 6,868 | Missing | Missing | 718,046 |
| 2017 | 715,291 | 7,324 | Missing | Missing | 715,291 |
| 2018 | 682,969 | 7,393 | Missing | Missing | 682,969 |
| 2019 | 640,257 | 6,663 | Missing | Missing | 640,257 |
| 2020 | 490,713 | 5,382 | Missing | Missing | 490,713 |
| 2021 | 730,824 | 6,453 | 4,278 | 4,044 | 6,833 |
| 2022 | 1,061,788 | 9,882 | 6,458 | 5,826 | 7,030 |
| 2023 | 1,028,074 | 9,731 | 6,366 | 5,667 | 5,706 |
| 2024 | 717,094 | 7,174 | 4,601 | 4,162 | 4,899 |
| 2025 | 582,241 | 7,930 | 5,069 | 4,620 | 4,539 |

2016–2020 have no employer organisation number in **any** source record. Their primary rates and denominators remain missing with `EMPLOYER_ID_GAP`. Exact validated-name results are a separately labelled sensitivity and must not be spliced into the primary series. Name fallback coverage also changes sharply between 2017 and 2018: the retained unresolved counts fall from 4,100 to 72, so fallback trends need employer-name coverage validation.

The spell Parquet contains 24,196 primary recruitment-spell representatives. Original ads, including review/sensitivity cases, remain in the ad-level Parquet. Annual raw-ad spell counts refer to spells observed among that year's ads; a spell spanning New Year can appear in two annual counts. Separate spell panel/trends assign each representative to its first-ad year. Duplicate ad IDs flagged: 82. The municipality-year panel has **8,700 rows**: 290 municipalities × 10 years × (5321, 5330, overall). Select one occupation_group before a municipality/year policy linkage.

## Preliminary machine output, not validated findings

| Year | Required / ads | Ad share | Fixed-composition share | Vacancy share | Formal threshold share | Uncertain share |
| --- | --- | --- | --- | --- | --- | --- |
| 2021 | 1,782 / 4,278 | 41.7% | 41.3% | 46.4% | 1.40% | 9.3% |
| 2022 | 3,182 / 6,458 | 49.3% | 50.7% | 58.0% | 1.30% | 9.8% |
| 2023 | 3,447 / 6,366 | 54.1% | 54.4% | 52.5% | 1.71% | 12.0% |
| 2024 | 2,578 / 4,601 | 56.0% | 56.7% | 68.7% | 1.02% | 15.6% |
| 2025 | 2,947 / 5,069 | 58.1% | 60.0% | 63.8% | 1.60% | 21.5% |

The fixed-composition measure uses constant 0.5/0.5 weights for SSYK 5321/5330, after estimating each occupation separately. These rates should not be interpreted as a demonstrated historical change until human validation and coverage checks are complete. Uncertain wording increases across the observed period. Formal categories can overlap and no educational/CEFR equivalences are imposed.

The primary ads contain 167,654 known positive vacancies; 4 ads have missing/invalid vacancy counts. Known-count weighting and explicit missing-as-one sensitivity are both exported. Recruitment-spell and 30-day repost-window estimates are separate outputs.

## Municipality coverage

| Year | GOOD ≥20 | MODERATE 5–19 | SPARSE 1–4 | NO_ADS |
| --- | --- | --- | --- | --- |
| 2021 | 74 | 116 | 67 | 33 |
| 2022 | 103 | 113 | 36 | 38 |
| 2023 | 101 | 112 | 34 | 43 |
| 2024 | 64 | 137 | 50 | 39 |
| 2025 | 78 | 121 | 45 | 46 |

Counts here refer to the overall occupation population. `NO_ADS` means zero **eligible observed ads**, not absence of recruitment. Its shares are missing. Candidate/mixed/uncertain counts explain exclusions; department-wide LSS boilerplate can conservatively move an elderly-care ad to `mixed`. The pilot's Uppsala 2024 case illustrates this limitation. Primary estimates may therefore omit otherwise relevant jobs until context review resolves them.

## Validation status

The reproducible review workbook contains **600 stratified ads** plus **300 randomly selected primary ads classified negative**. There are **4 overlapping ads**, so the two sheets cover **896 distinct ads**. They are different review designs and are not pooled for accuracy estimates.

Strata: {"no_requirement": 150, "strong": 100, "preferred_support": 100, "functional_generic": 100, "rejected_candidate": 100, "formal": 50}.

Period distribution: {"2022–2025": 227, "2019–2021": 195, "2016–2018": 178}. Occupations: {"5321": 339, "5330": 261}. Employer methods: {"employer_name_validated": 302, "orgnr_exact": 298}. Recruitment-volume bands proxy employer size, not municipal population. Cells include inclusion probabilities/design weights.

**Human-coded ads: 0.** Precision/PPV, recall/sensitivity, specificity, F1 and confusion-matrix cells remain unobserved. The 95% precision target has not been established. Negative-only review estimates false omission FN/(FN+TN); it cannot alone estimate sensitivity or FN/(TP+FN). The five agent-inspected pilot examples are development evidence, not gold-standard labels. Existing human coding is protected on regeneration and workbook import.

AF comparison among primary ads: ours+/AF+ **1**; ours+/AF− **13,935**; ours−/AF+ **42**; ours−/AF− **12,794**. There are **13,977 discordant cases**, with a seeded **50-ad review sample** and complete discordance CSV. AF tagging and text classification are both subject to review; neither is treated as truth.

## Schema and historical comparability

All ten annual files contain publication dates in all 12 months. Annual-file publication-year mismatches/missing dates total **20,399** and are excluded from their nominal partition, not silently reassigned. This does not establish that the correct annual archive contains each excluded record. Whole-file source and field inventories are preserved.

After the full run, bounded 1 MiB probes of official **2006, 2010 and 2015** original archives parsed 20 records each. All 60 had original text/dates but lacked standard `id`, employer organisation numbers and populated modern occupation-group identifiers. The original `occupation_group` is an object rather than the enriched list. Alternative legacy IDs/codes may support a future adapter but have not been validated. These first-record probes cannot estimate archive-wide missingness or terminology. **2006–2015 remains disabled and is not combined.** 2026 was not processed and remains excluded from complete-year trends.

The employer mapping is an official current cross-section. Historical validity endpoints are unknown, not invented; historical continuity is explicitly unverified. A 2006 extension also requires historical municipality-code treatment, including Heby.

## Sources and provenance

* [Arbetsförmedlingen/JobTech full enriched annual archives](https://data.jobtechdev.se/annonser/historiska/berikade/kompletta/), `2016_beta1_jsonl.zip` through `2025_beta1_jsonl.zip`.
* [Original historical archive](https://data.jobtechdev.se/annonser/historiska/) for bounded comparability probes only.
* [SCB municipality codes and names](https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/).
* [Skolverket legal-entity register export](https://api.skolverket.se/skolenhetsregistret/export/skolenhet/adressfil), legal form Kommuner, joined by legal name to SCB. School geography was not used as employer identification.

URLs, filenames, SHA-256, retrieval time, source beta version, ETag and Last-Modified are in `source_inventory.json` and source manifests. Raw archives remain in the ignored local cache, outside Git and the delivery bundle. No third-party job-site scraping or LLM calls were used.

## Reproduction and files

`README.md` gives installation and all download, employer-master, classification, review-import, panel and export commands. Occupation filtering is part of the streaming classification command. The completed run was created in `output-full-v1` and delivered at the requested canonical `research/recruitment_ads/output/` after preserving previous outputs. `output-v1/` retains the distinct classifier-1.1 pilot.

The full scan's code is frozen at commit `9efb5f809f3779e6a2437dbcbfe48e742b19922e`, with exact byte snapshots in `classification_source/` and module hashes/config/input hashes in `run.json`. Classification fingerprint: `f74d9d5f4317d5e65b6f1c425dde9cd961fbac0bf424149d3d0ed5b3a3c3a96d`. Restore these snapshots before resuming that checkpoint; a changed code/config fingerprint correctly requires a new output directory. Final export-module hashes are recorded separately. Later changes rename the geographical-scope flag without asserting representativeness and permit future configured non-core occupations; they do not change this frozen core cohort.

Required files: `ads_classified.parquet`, `recruitment_spells.parquet`, `municipality_year.parquet`, `validation.xlsx`, `recruitment_language_requirements.xlsx`. Also supplied: per-hit evidence, spell panel, CSV/JSON tables, review CSVs, inventories, six PNG/SVG figures, exact source snapshot and environment freeze. Raw archives are intentionally omitted from the portable bundle.

The analysis workbook contains all 11 requested sheets plus Spell_Trends and Sensitivity. Summary CSVs live under `tables/`, preventing a case-insensitive Windows collision with manually coded `validation.csv`. Human review remains in the root review files.

## Verification

Local lint passes; formatting passes; **110 tests pass**, including difficult Swedish fixtures, semantic exclusions, employer/occupation rules, denominators/missingness, vacancy weights, deterministic sampling, output schema, review preservation and a synthetic end-to-end run/resume/export. Previous commit CI passed on Windows/Ubuntu and Python 3.11/3.12/3.13. Final workbook checks compare saved totals and identities with source tables, preserve blank human labels and inspect formula errors and rendered sheets. No empirical accuracy or historical exhaustiveness is claimed.
