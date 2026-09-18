# Local validation

Validated locally on 16â€“17 September 2026 with Python 3.12.14 on Windows.

- 52 tests passed, exercising the real OpenAI Python SDK 2.54.0 through mocked HTTP.
- Tests include the complete HTML/PDF pipeline and offline replay with identical classifications.
- Ruff lint and formatting checks passed; installed dependencies passed `pip check`.
- Source distribution and wheel built successfully.
- All ten sheets of the synthetic example workbook were rendered and visually inspected.
- Workbook round-trip checks found no formula or error cells from source text.
- The example run passed verification of its 69 artifact hashes.

The example's municipalities, policies and model responses are synthetic. No live
OpenAI API classification or substantive research conclusions have yet been validated.

The original 50-test suite passed all six Linux/Windows and Python 3.11â€“3.13 jobs in
[GitHub Actions](https://github.com/Mihagley/Scraping_Swedish_elderly_care/actions/runs/35157737636).
The live collection workflow downloaded official municipality sources and committed
raw files, extracted text, JSON, Excel and audit artifacts. Its first preserved run
contains a stale AlingsÃ¥s URL returning an HTTP-200 error page; see `research/README.md`.
Two regression tests now cover preserving and excluding recognized error pages.

Exact installed dependency versions are in `requirements.lock.txt`; each run manifest
records its environment, source hashes and artifact hashes. Consult `research/latest.json`
for the latest real-source run; do not confuse it with `example-output/`.
