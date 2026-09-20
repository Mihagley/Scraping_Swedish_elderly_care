# Pilot execution report

The separate recruitment pipeline was exercised on ten municipalities and the complete official annual archives for 2017, 2021 and 2024. These are **unvalidated recruitment-wording measures**, not national estimates and never evidence of formal municipal policy.

## Counts

| Year | Source records scanned | Retained candidates | Primary eligible ads | Eligible recruitment spells |
|---|---:|---:|---:|---:|
| 2017 | 715,291 | 995 | Missing: employer identifier gap | Missing |
| 2021 | 730,824 | 883 | 653 | 612 |
| 2024 | 717,094 | 954 | 705 | 617 |
| Total | 2,163,209 | 2,832 | 1,358 observed in 2021/2024 | 1,229 |

No duplicate ad IDs occurred in the retained pilot. 2017 retains 309 validated-name matches and 686 unresolved employer-name candidates. Exactly 161 of the 2017 name matches satisfy occupation/context eligibility for the separate sensitivity population. The primary population never silently includes these name matches.

The primary occupation denominators are 619 (5321) and 34 (5330) in 2021, and 608 (5321) and 97 (5330) in 2024. Known vacancy totals are 4,755 and 12,043; all primary pilot ads have usable positive counts, so missing-as-one sensitivity equals known-count weighting in this particular pilot.

## Validation and limitations

The stratified review has 600 ads: 105 from 2017, 223 from 2021, 272 from 2024; 465 SSYK 5321 and 135 SSYK 5330. Its effective strata are 33 formal-category hits, 119 strong qualitative, 119 functional/generic, 41 preferred/support, 119 rejected candidates and 169 no-requirement/no-hit ads. Rare-stratum shortfalls were redistributed deterministically and are recorded in the sampling inventory.

The separate random predicted-negative review has 300 ads (149 from 2021 and 151 from 2024). It overlaps the stratified review by 102 ads: 900 review entries represent 798 unique advertisements. It estimates false omission among predicted negatives, not sensitivity by itself.

**Human-coded ads: 0.** Observed precision, recall, specificity and F1 are unavailable. No 95% precision claim is made. Synthetic tests and agent-assisted development inspection are not a gold standard.

The v1.1 classifier detects required wording in 337/653 ads in 2021 and 402/705 in 2024. These are software outputs, not validated findings. Uncertain hits occur in 60 and 199 primary ads respectively; that imbalance can affect comparisons over time. The small 5330 denominators make fixed-weight estimates particularly sensitive. Required formal thresholds occur in one primary 2021 ad and zero primary 2024 ads; this narrow pilot cannot establish municipal or national absence of formal thresholds.

All 1,358 primary ads have empty original AF `must_have.languages` lists. AF tagging is therefore negative in the exported comparison, including all 739 text-positive discordant cases. This does not establish that AF or our classifier is correct. A preserved 50-case discordance sample is available for separate inspection.

The context review also found a substantive limitation: Uppsala has 51 retained candidates in 2024, yet no primary-eligible ads because broad care-department descriptions trigger mixed context. Those ads remain available for review and sensitivity analysis. NO_ADS refers to the primary eligible denominator, not proof of no recruitment. Candidate and mixed-context counts are included in the revised coverage exports.

Seven of the ten municipalities have at least 20 primary ads in each of 2021 and 2024. Missing denominators remain missing shares. 2017 has EMPLOYER_ID_GAP for all ten municipalities; other unprocessed years remain NOT_PROCESSED.

## Schema and source coverage

All 715,291 records in the 2017 file lack employer organisation numbers. The 2021 file has 6,833 missing organisation numbers and 176 missing vacancy fields across the entire archive. The 2024 file has 4,899 missing organisation numbers and 100 missing vacancy fields. Missingness totals are whole-archive counts, not pilot-population counts.

The 2021 archive includes 2,538 publication-year mismatches/missing dates; 2024 includes 4,201. These records are counted and excluded from their nominal annual partitions. It has not been established that other annual files recover every such record. All twelve publication months occur in each inspected pilot archive. No historical exhaustiveness is claimed.

Sources are the [official JobTech complete enriched annual archives](https://data.jobtechdev.se/annonser/historiska/berikade/kompletta/), [SCB municipality codes](https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/) and [Skolverket municipal legal entities](https://api.skolverket.se/skolenhetsregistret/export/skolenhet/adressfil). The employer crosswalk covers all 290 municipalities, but its historical validity intervals remain unknown and explicitly flagged.

See `source_inventory.json`, individual download manifests and `run.json` for URLs, filenames, complete hashes, retrieval timestamps, publication-month counts and code/config identity. See `SCHEMA_COMPARABILITY.md` for period-specific limits.

## Reproduction and checks

Pilot classifications are frozen at `recruitment-sv-1.1.0`, fingerprint `0977d6af2ab7aaf7770aa1f584c709155e3c645ace92b3d2be146ff33dbbcd63`. Their classifier code is preserved in Git commit `fdf7fbad0ed15c667666b4e3d24345fa09ee8e7a`. Later export-module fixes are recorded separately by `analysis_manifest.json`.

The post-pilot full-run classifier is version 1.2.0. It adds compound proficiency wording discovered in two pilot ads, preserves unresolved cases, and uses the same rules across all full-run years. Five development-inspection ad IDs are documented separately from human validation. Bounded-memory spell matching was checked against the complete pilot and reproduced all 1,229 spell records exactly.

Formatting and lint checks pass. All 109 repository tests pass, including synthetic end-to-end processing, immutable checkpoints, human-review preservation on case-insensitive filesystems, schema contracts, denominator/missingness rules, language semantics and deterministic sampling. The six pilot figures and workbook views were rendered and inspected; formula-error searches matched no errors. Full-text review cells retain original text but their displayed row height is only a preview.

The code, sources and generated artifacts remain separate from the municipal-policy research. The full national run and its own validation sample are reported separately.
