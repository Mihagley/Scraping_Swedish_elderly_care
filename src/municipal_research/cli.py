from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config, load_municipalities
from .export import export_workbook
from .pipeline import run_pipeline
from .storage import digest, read_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Auditable municipal website research")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Discover, download, classify, verify and export")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--municipalities", type=Path, required=True)
    run.add_argument("--run-dir", type=Path, required=True)
    run.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    run.add_argument("--collect-only", action="store_true")
    run.add_argument("--limit", type=int, help="First N municipalities, for a small pilot")
    export = commands.add_parser("export", help="Rebuild Excel from an existing run")
    export.add_argument("run_dir", type=Path)
    export.add_argument(
        "--output", type=Path, required=True, help="A new XLSX path outside the sealed run"
    )
    verify = commands.add_parser("verify-run", help="Check the saved artifact hashes")
    verify.add_argument("run_dir", type=Path)
    demo = commands.add_parser(
        "demo", help="Synthetic offline HTML/PDF and mocked API demonstration"
    )
    demo.add_argument("--run-dir", type=Path, required=True)
    demo.add_argument("--cache-dir", type=Path, default=Path("data/demo-cache"))
    demo.add_argument("--config", type=Path, default=Path("examples/language_requirements.yaml"))
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            config, municipalities = (
                load_config(args.config),
                load_municipalities(args.municipalities),
            )
            if args.limit is not None:
                if args.limit < 1:
                    parser.error("--limit must be positive")
                municipalities = municipalities[: args.limit]
            data = run_pipeline(
                config,
                municipalities,
                args.run_dir,
                args.cache_dir,
                offline=args.offline,
                refresh=args.refresh,
                collect_only=args.collect_only,
            )
            print(
                json.dumps(
                    {"workbook": str(args.run_dir / "results.xlsx"), "summary": data["summary"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2 if data["errors"] or any(s["needs_review"] for s in data["summary"]) else 0
        if args.command == "export":
            if args.output.resolve().is_relative_to(args.run_dir.resolve()):
                raise ValueError(
                    "Choose an output outside the sealed run so its checksums stay valid"
                )
            print(export_workbook(args.run_dir, args.output))
        elif args.command == "verify-run":
            manifest = read_json(args.run_dir / "manifest.json")
            artifacts = manifest.get("artifacts", {})
            if not artifacts:
                raise ValueError("Run has no completed artifact manifest")
            bad = [
                name
                for name, expected in artifacts.items()
                if not (args.run_dir / name).is_file()
                or digest((args.run_dir / name).read_bytes()) != expected
            ]
            if bad:
                raise ValueError("Missing or modified artifacts: " + ", ".join(bad))
            print(f"Verified {len(artifacts)} artifact hashes.")
        elif args.command == "demo":
            from .demo import run_demo

            run_demo(load_config(args.config), args.run_dir, args.cache_dir)
            print("Synthetic demonstration: " + str(args.run_dir / "results.xlsx"))
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
