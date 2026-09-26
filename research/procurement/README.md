# Procurement-language comparison

This module compares Swedish-language requirements in elderly-care procurement notices and their tender documents with the existing municipal-document and job-ad analyses. Procurement is a separate evidence layer: a tender requirement is not automatically a municipal policy and it is not a job-ad requirement.

## Coverage and source boundaries

The pipeline accepts all available years and records coverage per notice. It never turns a missing source file or missing attachment into a zero. The working source note for this project is:

* Upphandlingsmyndigheten (UHM) statistics cover registered Swedish procurement-advertising databases from the transition around 2020–2022. The official registration requirement took effect on 1 January 2021, so 2020 and any transitional records must be labelled `partial_or_transitional` until the supplied file metadata proves otherwise.
* 2018–2019 have no equivalent national UHM coverage in this project. They remain `not_available`, unless an identified source file is added.
* 2021–2022 can be analysed from UHM registered-database exports when supplied. Direct awards, LOV and documents outside the export are not silently counted.
* Later UHM open-data releases and TED notices can be added as separate sources. TED covers EU-threshold notices and therefore does not replace national below-threshold LOU coverage.

Every row keeps `source`, `coverage_status`, `document_status`, and the source/document URL. The summary reports counts, not percentages, unless the denominator is explicitly documented.

## Input and output

Put UHM, TED, or other licensed exports in a directory as UTF-8 CSV, JSON, or JSONL. The normalized fields are `notice_id`, `publication_date`, `buyer_name`, `municipality_name`, `title`, `document_text`, `document_url`, `coverage_status`, and `source_url`. Run:

```bash
PYTHONPATH=src python scripts/analyze_procurement.py \
  --input-dir research/procurement/raw \
  --output-dir research/procurement/outputs \
  --source uhm \
  --elderly-care-only
```

`--elderly-care-only` keeps rows whose CPV code is 85311100 (welfare services for the elderly) or whose title/description mentions hemtjänst, särskilt boende, äldreboende, vård- och omsorgsboende and similar terms. Without the flag every row in the input directory is analysed. `manifest.json` records both the raw row count and the retained count.

Outputs are `notices.csv`, `year_summary.json`, and `manifest.json`. The classifier stores a short evidence snippet for explicit staff requirements such as “god svenska”, “svenska i tal och skrift”, “tala, läsa och skriva svenska”, “behärska svenska”, “språkkrav”, “yrkessvenska”, Swedish course levels, and CEFR/Gers levels (B1–C2). A match in a sentence about the service user's language (interpreter, minority language, mother tongue) without a staff reference is downgraded to `language_related`, because tender documents often describe the user's right to an interpreter. `missing_document`, `no_evidence`, `language_related`, and `explicit_requirement` are separate categories for review.

## Review rules

Do not publish raw tender attachments unless their licence permits redistribution. Cite UHM as `Uppgifter: Upphandlingsmyndigheten | Bearbetning: Mihagley/Scraping_Swedish_elderly_care`, with the retrieval date and original URL. Keep the source file and a checksum in the local manifest when making a release. Inspect `language_evidence` before treating a notice as an explicit Swedish requirement.


## Automated pipeline (fetch → download → extract → classify)

`scripts/run_procurement_pipeline.py` runs the whole flow in one command:

```bash
PYTHONPATH=src python scripts/run_procurement_pipeline.py \
  --contact your.email@su.se \
  --sources ted,lov \
  --from-year 2018 --to-year 2025 \
  --output-dir research/procurement/pipeline
```

Sources:

* **TED** (`ted`): the open TED search API v3 (no key). Default query: CPV 85311100, 85311000, 85312100, 85144100, buyer country Sweden, publication date in the year range. Override with `--ted-query`. Only notices above the EU thresholds. The notice description is always classified; linked tender documents are downloaded when publicly reachable (skip with `--no-ted-documents`). Documents behind a login in commercial advertising databases end up as `missing_document`.
* **Hitta LOV-uppdrag** (`lov`), formerly Valfrihetswebben: adverts are discovered from the listing page, filtered to elderly care, and the linked municipality page is followed one hop to its tender PDFs. These are the *currently published* adverts (`coverage_status = lov_current_adverts`), dated by the advert's last update. For earlier years, add archived or municipal document URLs with `--lov-seed-file`, or use the manual input path above. If the listing only renders through JavaScript, `LOV: 0 annonser hittade` is printed; put advert URLs in a seed file instead.

Behaviour: all HTTP goes through `network.Fetcher` (robots.txt, per-host pacing via `--interval`, retries, cache, audit log in `audit.jsonl`). Re-running the same command resumes from the cache; `--offline` rebuilds outputs from the cache only. Outputs: `notices.csv`, `year_summary.json` (per source), `raw_rows.jsonl` (metadata and per-document records with SHA-256), and `manifest.json`. Downloaded PDFs and extracted text are stored under `documents/` and are git-ignored; check licences before sharing them.
