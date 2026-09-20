# Historical recruitment extension: 2006–2015 and the 2016–2020 employer-number gap

Executed 20 September 2026. Classifier `recruitment-sv-1.3.1`; adapter `historical-adapter-1.1.0`. These are **unvalidated recruitment-wording measures**, with no inference about formal municipal policy.

## What was added

The extension scanned **3,479,530 records** from all ten original annual files for 2006–2015. It retained **28,237 older candidates**: **2,226** enter the mapped-occupation sensitivity cohort and **3,753** enter a separate unmapped-title exploratory cohort. The rest remain available for employer/context/occupation review.

For 2016–2020, all **33,630** previously retained candidates are included, of which **15,680** enter the mapped sensitivity cohort through validated municipal legal-name matching and explicit elderly-care job context. Employer organisation numbers have not been imputed. The original exact-number primary series remains missing for those years.

The extension also includes 2021–2025 reference records under the same context rule and language classifier. Altogether it contains **103,037 retained records**, **46,295 eligible sensitivity/exploratory records**, **42,126 possible spell representatives**, and **40,600 municipality/year/cohort/occupation panel rows**. The original 2016–2025 output and portable bundle remain unchanged at classifier 1.2.

| Year | Retained | Mapped sensitivity | Title exploratory | Review only / duplicate |
|---|---:|---:|---:|---:|
| 2006 | 5,039 | 0 | 944 | 4,095 |
| 2007 | 3,425 | 16 | 1,060 | 2,349 |
| 2008 | 2,671 | 75 | 376 | 2,220 |
| 2009 | 1,341 | 33 | 135 | 1,173 |
| 2010 | 1,744 | 87 | 216 | 1,441 |
| 2011 | 1,934 | 41 | 262 | 1,631 |
| 2012 | 2,041 | 71 | 340 | 1,630 |
| 2013 | 2,011 | 94 | 314 | 1,603 |
| 2014 | 3,037 | 591 | 106 | 2,340 |
| 2015 | 4,994 | 1,218 | 0 | 3,776 |
| 2016 | 6,868 | 1,768 | 0 | 5,100 |
| 2017 | 7,324 | 1,916 | 0 | 5,408 |
| 2018 | 7,393 | 4,618 | 0 | 2,775 |
| 2019 | 6,663 | 4,283 | 0 | 2,380 |
| 2020 | 5,382 | 3,095 | 0 | 2,287 |
| 2021 | 6,453 | 3,968 | 16 | 2,469 |
| 2022 | 9,882 | 5,939 | 0 | 3,943 |
| 2023 | 9,731 | 5,844 | 0 | 3,887 |
| 2024 | 7,174 | 4,172 | 0 | 3,002 |
| 2025 | 7,930 | 4,697 | 0 | 3,233 |

The two analysis cohorts must remain separate. Select `historical_cohort` and `occupation_group` before a municipality/year join. `review_only` rows are not analysis denominators. Missing official IDs in older records are represented by stable `source-record:` locators built from archive hash, member and line. Those identify observed source records, not recovered official advertisement IDs. Possible spells provide a separate reposting sensitivity; they do not establish unique vacancies or hiring events.

## Official sources and comparability

* [Arbetsförmedlingen original historical files](https://data.jobtechdev.se/annonser/historiska/): `2006.jsonl.zip` through `2015.jsonl.zip`, full annual archives, original JSONL representation last modified in May 2023.
* [Official JobTech legacy occupation crosswalk](https://taxonomy.api.jobtechdev.se/v1/taxonomy/legacy/get-occupation-name-with-relations), retrieved 2026-09-20T06:46:40.800351+00:00. SHA-256 `c19d16114fec3882572f8bdd23d345c1ed86cec7ae336811db6b66ce9a13b4b7`. This is a retrieval snapshot of the legacy endpoint, not a claim that its classifications were historically constant.
* [Enriched complete archives](https://data.jobtechdev.se/annonser/historiska/berikade/kompletta/) supplied the frozen 2016–2025 candidate records. Their input hash is recorded in `run_historical.json`.
* The existing 290-municipality master uses official SCB municipality identifiers and Skolverket municipal legal entities. Exact validated legal-name aliases are separately marked, with unknown historical validity intervals left blank.

All ten older files contain observations in all twelve publication months. This establishes successful reading of the supplied annual files, **not exhaustive historical recruitment coverage**. Source URLs, filenames, SHA-256 hashes, retrieval timestamps, ETags, last-modified dates, versions and per-month counts are in `historical_source_inventory.json` and the run manifest. Older annual sources retain the conservative `calendar_complete=false` flag used by the main downloader; this extension does not promote them to primary complete-year trend eligibility.

| Year | Source records | Missing official ID | Missing employer orgnr | Missing source SSYK group |
|---|---:|---:|---:|---:|
| 2006 | 245,227 | 245,210 | 245,211 | 245,227 |
| 2007 | 284,105 | 284,075 | 284,075 | 284,105 |
| 2008 | 265,223 | 265,200 | 265,200 | 265,223 |
| 2009 | 196,124 | 196,099 | 196,099 | 196,124 |
| 2010 | 289,254 | 289,221 | 289,221 | 289,254 |
| 2011 | 364,688 | 364,653 | 364,653 | 364,688 |
| 2012 | 379,358 | 379,358 | 379,358 | 379,358 |
| 2013 | 390,053 | 390,053 | 390,053 | 390,053 |
| 2014 | 451,985 | 451,985 | 451,985 | 451,985 |
| 2015 | 613,513 | 613,513 | 613,513 | 613,513 |

These are whole-file missingness counts. Headline, description, dates, vacancy counts and original occupation/legacy structures are retained at candidate level; candidate-field missingness is recorded in `HISTORICAL_RUN_COUNTS.json`. Missing AF enrichment remains unknown, not false. **All 28,237 retained older records lack usable vacancy counts**, so their known-count vacancy-weighted estimates remain missing. Employment-duration labels are present in these retained cases but have not been harmonised across source generations.

The legacy occupation-title code is not an SSYK group. The official crosswalk maps 7586 and 1457 to 5321, and 5797/5799/5713/5707 to 5330. Unknown code 0 and legacy code 5706 do not receive a guessed modern SSYK. Ads with an explicit undersköterska/vårdbiträde title, validated employer and unambiguous elderly-care context may enter the title-only cohort while `occupation_code` remains missing. Ambiguous/conflicting mapped occupations stay in review.

Both cohorts require explicit positive elderly-care job context in every year, including 2021–2025 reference ads. LSS/personal-assistance or other conflicting contexts remain excluded or mixed. This stricter context rule means extension counts should not be substituted for the original primary counts. The current crosswalk and changing employer-name coverage cannot establish a stable historical sampling population. Employer department names remain unresolved until an alias is independently validated; workplace location never identifies the employer.

Municipality keys identify current-master legal entities, with `municipality_id_basis=current_master_legal_entity_key_historical_continuity_unverified`. They are not reconstructed historical geography. Original workplace codes remain separate, including cases such as Heby's pre-2007 code. Historical legal-name/organisation continuity is an assumption requiring further evidence.

**No combined 2006–2025 primary trend is produced.** Tables explicitly identify original legacy years, employer-number-gap years and reference years. Figures use separate cohort panels and break lines at source-period boundaries. Formal courses, SFI, CEFR levels and tests are never treated as equivalent. Category shares can overlap. Vacancy weighting uses known positive counts, with missing-as-one labelled separately; no-ads rates remain missing.

## Pilot, rule checks and validation

The initial older pilot processed 2006, 2010 and 2015: 1,147,994 source records, 11,777 retained candidates, 1,305 mapped sensitivity and 1,160 title exploratory cases. Its files remain in `output-historical-pilot-v1`. Review of original snippets found carriage-return-only line breaks and the spelling `utrycka` in an explicit Swedish proficiency requirement. Classifier 1.3.1 preserves offsets when splitting CR/CRLF/LF lines, recognises this spelling, and ties application-language exclusions more closely to the application instruction. A further development check isolated conditional requests to attach existing grades and ambiguous training-admission requirements; these are now application instructions or uncertain, rather than mandatory employee thresholds. The same version reclassifies all recent reference records in the extension. Exact frozen classifier modules, configuration and hashes are saved under `reproduction/` and `run_historical.json`.

The new workbook contains **600 stratified eligible records from 2006–2020**, **300 separately sampled predicted negatives**, and **150 excluded-case reviews**. There are **1044 unique records**, including **6** overlaps between the stratified and negative samples. Seed 20260918, effective strata, sampling cells, inclusion probabilities and design weights are preserved. Periods include 2006–2010 and 2011–2015. Recruitment-volume bands are used for balancing; they are not municipal population-size measures. The excluded review sample is for selection-rule inspection, not for pooled language-accuracy estimates.

**Human-coded records: 0. Precision, recall, specificity, F1 and false-negative performance remain unknown.** No 95% precision claim is made. Development inspection and synthetic tests are not a gold standard. Both recent and historical terminology require human adjudication before these measures are analytically reliable. Current false-negative limitations include unusual wording, qualification lists without clear cues, missing occupation codes, generic job titles and unvalidated historical employer names. All uncertain categories and original text are retained for review.

Validation checks: 129 tests pass, including the small synthetic end-to-end historical run, deterministic restart and preservation of existing review labels; lint and formatting checks pass. Runtime assertions confirm that older rows never become primary, unmapped titles have no imputed SSYK, no-ad rates are missing, panel keys are unique, spell member totals reconcile, all records use classifier 1.3.1, and the original primary Parquet hash is unchanged. Workbook reconciliation and CI details are included in the delivery's `verification/` directory.

## Restart, review and export commands

Run from the repository root after the installation in [README.md](README.md). Complete the original 2016–2025 run first so `research/recruitment_ads/output/ads_classified.parquet` and `source_inventory.json` exist. Employer identification and occupation filtering are performed during the historical streaming pass; preserved mapped/title/review categories are separate outputs of that pass.

```sh
python scripts/download_historical_taxonomy.py
# First inspect a three-year older pilot in its own directory:
python scripts/build_historical_extension.py --download --years 2006 2010 2015 --output research/recruitment_ads/output-historical-pilot-new
# After reviewing the pilot, run all ten original annual archives:
python scripts/build_historical_extension.py --download --years 2006 2007 2008 2009 2010 2011 2012 2013 2014 2015 --output research/recruitment_ads/output-historical-v2
python scripts/export_historical_extension.py --output research/recruitment_ads/output-historical-v2
```

The build automatically produces stratified/negative/excluded review CSVs and municipality panels. The final command creates the workbooks and six figures. Re-running the build verifies the inputs/code fingerprint and checkpoint hashes, resumes completed years, and preserves human CSV labels. Changed inputs or rules require a new output directory. Existing raw sources are checksum-verified, never silently replaced. Use a new taxonomy path when refreshing its official snapshot. No LLM calls are made.

After editing the delivered review workbook (read complete text in the cell editor or CSV; row height is only a preview):

```sh
python scripts/build_historical_extension.py --output research/recruitment_ads/output-historical-v2 --export-only --import-workbook path/to/reviewed-validation.xlsx
python scripts/export_historical_extension.py --output research/recruitment_ads/output-historical-v2 --skip-review-workbook
```

Workbook imports check record/text identity, refuse conflicting human labels and keep hashed CSV backups. The export-only command uses the run's saved config/master. The portable Python workbook exporter reproduces the contents; the delivered workbook styling is created with the bundled artifact tool and its builder is included in the portable package. Never overwrite an edited validation workbook. If directly editing review CSVs, omit `--import-workbook` and use `--export-only` before export.

## Output paths

All new working outputs are in `research/recruitment_ads/output-historical-v2/`:

* `ads_historical.parquet`: all retained candidates with original text, evidence JSON, employer method, occupation mapping and cohort flags.
* `recruitment_spells_historical.parquet`: first-record representatives of eligible spells, with member IDs and within-spell changes.
* `municipality_year_historical.parquet` and `municipality_year_spells_historical.parquet`: separate record/spell panels (290 × 20 × 7 rows each).
* `recruitment_language_requirements.xlsx`: 13 analysis/audit sheets; `validation.xlsx`: four review/instruction sheets.
* `validation.csv`, `false_negatives.csv`, `excluded_cases_review.csv`, AF discordances, employer/occupation inventories, machine-readable tables, six PNG/SVG figure pairs, source inventories and run manifests.

The portable delivery is `outputs/recruitment_ads_historical_2006_2025/` with a companion ZIP, file checksums and reproducibility sources. Huge annual archives and processing checkpoints are excluded from that bundle and from Git. The working cache retains the original downloads. The original `outputs/recruitment_ads_2016_2025/` delivery is preserved.
