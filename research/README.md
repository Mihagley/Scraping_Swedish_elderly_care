# Research data

This directory holds real-source research. `example-output/` at repository root is a
separate synthetic demonstration and must not be treated as municipal evidence.

## Run in GitHub

1. For classification, add `OPENAI_API_KEY` under repository **Settings → Secrets and
   variables → Actions**. The key is never committed or printed by the runner.
2. Open **Actions → Research pilot → Run workflow**.
3. Choose `collect` to download and extract sources without API calls, or `classify`
   for independent structured model passes, quote checks, verification and adjudication.

Changing `research/request.json` also triggers a run using its `mode`. The initial
request is collection-only. Runs are bounded to the six supplied municipalities, one
targeted candidate document each, at most 20 chunks per document, and at most 200 API
invocations in classification mode. These bounds can be edited in the request file.
The general CLI still supports broader discovery; this pilot uses manually discovered
official source URLs to establish an inspectable baseline first.

The workflow commits results to `research/results/<run>-<attempt>-<mode>/` and updates
`latest.json` and `STATUS.md`. Each directory contains raw source files, cleaned text,
page maps, full JSON, the Excel workbook and the audit trail. Caches stay outside Git
and are restored through Actions cache. The run is also available as an Actions
artifact for 90 days. Keep the committed run for durable source preservation.

An error or review flag is not a negative answer to the research question. A collection
run reports `NotClassified`, even if a source looks relevant. Source selection is a
small pilot, not an exhaustive review of these municipalities or all 290 municipalities.

## Initial seed provenance

The URLs in `municipalities-pilot.csv` were found through Codex web search on
2026-09-16. They are candidates, not pre-approved findings. The downloader independently
retrieves each candidate, records its final URL and content hash, and applies robots rules.

- Alingsås: council protocol extract, 19 June 2023, concerning language requirements.
- Stockholm: elderly-care administration response dated 26 May 2023, presented to the board in August 2023.
- Kungsbacka: care-board minutes from 12 June 2025, referring to an earlier council decision. Later reporting needs explicit historical support.
- Sundsvall: care-board protocol dated 15 December 2022, including language verification.
- Linköping: 2025 budget attachment discussing recruitment language testing and a 2024 pilot.
- Västerås: current municipal page about language in care; its existence alone cannot establish a pre-2025 start date.

Source dates above describe the documents, not the implementation dates of a requirement.
Exact source URLs are preserved in the CSV and each run. Never copy classifications
from the earlier conversation into this dataset without rechecking the retrieved evidence.
