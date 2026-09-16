# Local validation

Validated on 16 September 2026 with Python 3.12.14 on Windows.

- 47 tests passed, exercising the real OpenAI Python SDK 2.54.0 through mocked HTTP.
- Tests include the complete HTML/PDF pipeline and offline replay with identical classifications.
- Ruff lint and formatting checks passed; installed dependencies passed `pip check`.
- Source distribution and wheel built successfully.
- All ten sheets of the synthetic example workbook were rendered and visually inspected.
- Workbook round-trip checks found no formula or error cells from source text.
- The example run passed verification of its 69 artifact hashes.

The example's municipalities, policies and model responses are synthetic. No live
OpenAI API calls, live municipality crawl, or substantive research conclusions were
validated. The configured Linux/Windows and Python 3.11–3.13 CI matrix has not been run
on GitHub from this workspace. Exact installed dependency versions are in
`requirements.lock.txt`; the example manifest records its environment and source hashes.
