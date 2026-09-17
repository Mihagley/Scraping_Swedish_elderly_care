from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from municipal_research.config import load_municipalities
from municipal_research.sharding import build_shard_plan
from municipal_research.storage import canonical, digest, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the municipality registry and build matrix shards")
    parser.add_argument("--municipalities", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--expected", type=int, default=290)
    parser.add_argument("--target-size", type=int, default=25)
    parser.add_argument("--min-size", type=int, default=20)
    parser.add_argument("--max-size", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    municipalities = load_municipalities(args.municipalities)
    plan = build_shard_plan(
        municipalities,
        expected=args.expected,
        target_size=args.target_size,
        min_size=args.min_size,
        max_size=args.max_size,
    )
    input_parts = [plan["registry_sha256"]]
    if args.request:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        config_path = Path(request["config"])
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        if not config_path.is_file():
            raise ValueError(f"Configured research file does not exist: {config_path}")
        plan["request_sha256"] = digest(args.request.read_bytes())
        plan["config_sha256"] = digest(config_path.read_bytes())
        input_parts.extend([plan["request_sha256"], plan["config_sha256"]])
    plan["input_sha256"] = digest(canonical(input_parts))
    write_json(args.output, plan)

    matrix = {
        "include": [
            {"index": shard["index"], "id": shard["id"], "size": shard["size"]}
            for shard in plan["shards"]
        ]
    }
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            handle.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
            handle.write(f"registry_sha256={plan['registry_sha256']}\n")
            handle.write(f"input_sha256={plan['input_sha256']}\n")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
