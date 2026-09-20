# Regional recruitment-language trends

The regional extension groups municipal employers into Sweden's **21 counties (län)**, using the official SCB 2026 municipality/county snapshot already cached for the employer master. It covers all 290 municipality keys. County membership is held fixed across 2006–2025, including Heby under current Uppsala county. It does not reconstruct historical county boundaries or add regional-government employers. Workplace location does not assign the employer's county.

Source: [SCB, counties and municipalities in code order](https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/), cached 18 September 2026; SHA-256 `01e2e57890cbd0a227df052f75a53c713dd0fee72ce107ab4efd43d53d630a40`. The 2026 grouping was checked against the live official page on 20 September 2026. The full cached HTML and a 290-row municipality/county table accompany the regional outputs.

## Measures and scope

The numerator counts eligible ads classified as explicitly requiring Swedish. The denominator contains all eligible ads within the same county, year and cohort. County rates pool the underlying records; **municipality percentages are not averaged**. Original source-record locators remain locators where official advertisement IDs are missing.

Mapped-occupation sensitivity and unmapped-title exploratory cohorts remain separate. The occupation-standardised rate applies the frozen run's weights (0.5 each for SSYK 5321 and 5330) within each county/year and stays missing if either occupation has no denominator. There is no standardised rate for unmapped titles. Courses, CEFR levels, SFI, tests and qualitative categories remain separate columns, alongside preference, uncertainty and vacancy-weighting measures.

All **46,295 eligible records** and **42,126 possible recruitment-spell representatives** in the frozen historical extension are accounted for. The regional panel has **2,940 rows**: 21 counties × 20 years × seven cohort/occupation combinations. The main Region_Year table has 840 rows (two separate cohorts per county/year). County ad counts and requirement/preference numerators reconcile exactly with the existing national tables. No advertisements are reclassified and neither previous delivery is replaced.

Missing denominators remain missing. A coverage flag describes observed record volume, not representative county coverage. Tables additionally show the number of municipalities with observed ads, the number with at least 20 ads, and the county's total number of municipalities. Sparse estimates can be extremely unstable. For example, Gotland has only one eligible mapped record in 2025; its plotted percentage must not be treated as a reliable county estimate.

The historical periods remain distinct: 2006–2015 original legacy files, 2016–2020 employer-number gaps and 2021–2025 reference records. **The regional results remain unvalidated recruitment-wording measures.** Differences may reflect employer-name availability, occupation coding, classifier errors and which municipalities are observed. No recruitment-ad result establishes a formal municipal policy.

## Plots and tables

Four figures are provided as PNG and SVG:

1. `01_county_trends_mapped`: one trend panel per county, using the observed ad-weighted rate. Hollow points indicate fewer than 20 ads.
2. `02_county_trends_standardised`: the fixed occupation-weight version. Hollow points indicate fewer than five ads in at least one occupation; both occupation denominators must be present.
3. `03_county_trends_exploratory`: title-only cases, separately plotted.
4. `04_county_year_overview`: a compact county/year heatmap for mapped occupations. Grey cells have no eligible ads, orange dots have fewer than 20, and colour encodes the percentage requiring Swedish.

Lines stop at missing denominators and the 2015/16 and 2020/21 source-period boundaries. All panels use the same 0–100% scale. The heatmap is a compact descriptive overview, not evidence that the historical cohorts are interchangeable.

`regional_language_requirements.xlsx` contains Region_Year, Region_Occupation, Spell_Trends, Coverage, Municipality_County, Methodology and Sources. CSV copies are in `tables/`; full record and spell panels are `region_year.parquet` and `region_year_spells.parquet`. The Spell_Trends table counts first-record spell representatives, so its n_ads denotes the number of those representatives. The raw-ad annual spell count may include a spell in more than one year.

## Reproduction

After installing the repository dependencies, run from the repository root:

```sh
python scripts/build_regional_recruitment.py --source research/recruitment_ads/output-historical-v2 --output research/recruitment_ads/output-regions-v2
python scripts/plot_regional_recruitment.py --output research/recruitment_ads/output-regions-v2
```

The input is the completed, frozen historical run. No annual redownload or language reclassification is required. Use `--scb-html` to supply the cached official HTML at another path. The command records source-file, mapping and implementation hashes in `run_regional.json`. Changed inputs or aggregation code require a new output directory. A smaller processed period retains unprocessed years as missing; the same script can handle such a source run.

The portable Python exporter reproduces workbook contents. The delivered workbook uses the same bundled artifact-tool builder as the earlier outputs, with formula-driven ad shares and occupation standardisation and a separate regional output mode. `--tables-only` prepares its input without creating an additional workbook. The builder is included with the portable delivery for provenance.

Tests cover county parsing, exact municipality keys, fixed current geography, employer rather than workplace assignment, record-weighted aggregation, occupation standardisation, missing denominators, cohort separation, exclusion of duplicate/ineligible rows and invalid mapping/weights. The full suite contains **135 passing tests**; lint and formatting checks pass. The real-data build also independently reconciles county counts and numerators to the frozen national tables.

Working outputs: `research/recruitment_ads/output-regions-v2/`. Portable delivery: `outputs/recruitment_ads_regions_2006_2025/`. The regional package contains the workbook, figures, tables, manifests, mapping provenance and reproduction code, without duplicating full advertisement text or annual archives.
