#!/usr/bin/env python3
"""Build reviewable procurement outputs from CSV, JSON and JSONL source files."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable

from municipal_research.procurement import aggregate_by_year, is_elderly_care, normalize_notice


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
    elif path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    elif path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        yield from (payload if isinstance(payload, list) else payload.get("notices", payload.get("results", [payload])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", default="local_export")
    parser.add_argument(
        "--elderly-care-only",
        action="store_true",
        help="Keep only rows whose CPV (85311100), title or description points to elderly care.",
    )
    args = parser.parse_args()
    inputs = [p for p in sorted(args.input_dir.rglob("*")) if p.suffix.lower() in {".csv", ".json", ".jsonl"}]
    rows = [row for path in inputs for row in _rows(path)]
    n_raw = len(rows)
    if args.elderly_care_only:
        rows = [row for row in rows if is_elderly_care(row)]
    notices = [normalize_notice(row, source=args.source) for row in rows]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "notices.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(notices[0].to_dict()) if notices else ["notice_id"])
        writer.writeheader()
        writer.writerows([notice.to_dict() for notice in notices])
    (args.output_dir / "year_summary.json").write_text(json.dumps(aggregate_by_year(notices), ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "manifest.json").write_text(json.dumps({"source": args.source, "n_raw_rows": n_raw, "elderly_care_only": args.elderly_care_only, "n_notices": len(notices), "inputs": [str(p) for p in inputs]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

