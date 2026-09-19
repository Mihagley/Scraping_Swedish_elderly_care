# Historical schema and comparability checkpoint

Checked directly against full official enriched `beta1` JSONL archives for 2017, 2021 and 2024, plus the official 2025 1% development sample. Files use a ZIP member containing one JSON object per line. The pilot archives use object-valued employer and description fields and list-valued occupation groups. The adapter also accepts object-valued occupation groups used by other JobTech representations.

| Field/concept | 2017 | 2021 | 2024 | Interpretation |
|---|---|---|---|---|
| Publication date, original text, ad ID | Present | Present | Present | Preserve originals and record year mismatches |
| employer.organization_number | Missing in every record | Mostly present | Mostly present | Primary exact-employer rates cannot span 2017 without an additional documented source |
| Employer legal name | Present | Present | Present | Exact validated fallback only; department/brand variants need review |
| SSYK group | occupation_group list | occupation_group list | occupation_group list | Read group legacy ID; preserve original occupation/job-title fields |
| Vacancy count | Field present | Some missing | Inspect inventory | No default imputation in primary vacancy weights |
| must_have/nice_to_have languages | Fields present | Fields present | Fields present | Preserve, compare and validate; enrichment quality is not assumed constant |
| Workplace municipality | Separate | Separate | Separate | Never used to identify a municipal employer |

`source_inventory.json` provides exact counts for field missingness, source records, publication months, retained ads and every eligibility exclusion. The checked 2021 file includes some out-of-year publication dates. Annual partition mismatches are excluded and counted, not silently reassigned or treated as exhaustive coverage.

Other 2016–2025 years remain **uninspected**, except the limited 2025 format sample. Their full field coverage is not inferred from neighbouring years. Download/stream support is not proof of comparability. Some archives have historically patched municipality variants; the chosen full enriched filename and hash explicitly identify which source was used.

2006–2015 is disabled pending separate schema, SSYK-version, employer, text, vacancy, terminology and historical municipality-code validation. The existence of downloadable old files is not evidence that they are comparable. No combining is implemented. 2026 has official quarterly files, is incomplete, and is excluded from complete-year estimates by default.

The checked-in employer master is a current cross-section. Unknown historical validity intervals are blank and identified as unverified. Neither exact current organisation-number matching nor language wording establishes a formal municipal policy.
