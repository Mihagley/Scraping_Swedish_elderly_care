# Historical schema and comparability checkpoint

The full official enriched `beta1` JSONL archives for **every year 2016–2025** were scanned. These files use a ZIP member containing one JSON object per line, object-valued employer/description fields and list-valued occupation groups. Original structures and text remain preserved. The adapter also accepts object-valued occupation groups used in original/API representations.

| Year | Source records | Missing employer orgnr | Missing vacancy count | Publication-year mismatch/missing |
|---|---:|---:|---:|---:|
| 2016 | 718046 | 718046 | 0 | 0 |
| 2017 | 715291 | 715291 | 0 | 0 |
| 2018 | 682969 | 682969 | 0 | 0 |
| 2019 | 640257 | 640257 | 0 | 0 |
| 2020 | 490713 | 490713 | 95 | 120 |
| 2021 | 730824 | 6833 | 176 | 2538 |
| 2022 | 1061788 | 7030 | 202 | 5753 |
| 2023 | 1028074 | 5706 | 273 | 4757 |
| 2024 | 717094 | 4899 | 100 | 4201 |
| 2025 | 582241 | 4539 | 82 | 3030 |

Every archive contains all 12 publication months. `source_inventory.json` records exact field-missing counts, eligibility exclusions, URLs, hashes and retrieval dates. Missing vacancy counts here describe source fields; primary weighting separately rejects invalid/nonpositive counts. Neither 12 months nor a complete-calendar filename establishes exhaustive recruitment coverage. Out-of-year records are excluded from their nominal partition and counted, not reassigned silently.

**2016–2020 cannot enter the primary exact-organisation-number trend.** Primary panel counts/rates stay missing with `EMPLOYER_ID_GAP`. Validated exact legal-name fallback is separately labelled. Name coverage itself changes between 2017 and 2018 and needs validation. Bounded probes of the original non-enriched 2017/2020 archives also found no employer numbers in their first 10 records each (`original_archive_schema_probes.json`). This does not establish archive-wide original-file missingness or rule out another official source.

Use `occupation_group.legacy_ams_taxonomy_id` for the SSYK group, not the different occupation-title legacy code. Preserve both structured values and original job titles. Workplace geography never identifies the municipal legal employer. Original AF language fields are preserved independently: the primary full cohort has 43 AF-positive tags, versus 13,936 text-classifier positives. Disagreement is an audit target, not a gold standard.

## Initial older-file assessment (superseded by the separate extension)

On 20 September 2026, bounded first-1-MiB HTTP Range requests were made to official original `2006.jsonl.zip`, `2010.jsonl.zip` and `2015.jsonl.zip`. Twenty complete records per year were parsed. Prefix hashes, URLs, member names, field types and sample counts are in `historical_extension_schema_probes.json`; prefix bytes are cached locally.

All 60 sampled records had original text, headlines and publication dates. Standard `id` and employer organisation numbers were absent, and sampled modern `occupation_group` identifiers were null. Occupation/group structures were objects, not enriched lists. Legacy attributes and external IDs may permit reconstruction, but uniqueness, stable population mapping and SSYK-version crosswalks have not been established. These ordered prefix samples are **not representative samples or archive-wide missingness estimates**.

That initial checkpoint kept 2006–2015 disabled in the primary pipeline. A subsequent **separate** extension now reads all ten original annual files, uses the official legacy occupation crosswalk where unambiguous, assigns source-record locators where original IDs are absent, and distinguishes validated legal-name matches. Full-file missingness counts, historical terminology checks and exact retained counts are in [HISTORICAL_EXTENSION.md](HISTORICAL_EXTENSION.md). Unknown occupation codes remain a separate title/context exploratory group. These checks do not establish historical exhaustiveness or population comparability; no older years have been merged into the primary trend. Current legal-entity municipality IDs are explicitly distinguished from historical workplace geography.

2026 has official quarterly files and is incomplete. It was not processed and is excluded from default complete-year estimates. A separately scoped partial-year adapter would be needed.

Neither these field checks nor advertisement wording provides evidence of a formal municipal language policy.
