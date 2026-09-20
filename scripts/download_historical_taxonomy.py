"""Cache the official legacy occupation crosswalk without replacing existing snapshots."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from recruitment_ads.download import sha256, write_json

URL = "https://taxonomy.api.jobtechdev.se/v1/taxonomy/legacy/get-occupation-name-with-relations"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output",
        default="research/recruitment_ads/cache/taxonomy/legacy_get-occupation-name-with-relations.json",
    )
    args = p.parse_args()
    path = Path(args.output)
    manifest = path.with_suffix(".json.manifest.json")
    if path.exists():
        if not manifest.exists():
            raise ValueError("Existing taxonomy has no manifest; use a new output path")
        meta = json.loads(manifest.read_text())
        if meta["source_url"] != URL or meta["source_hash"] != sha256(path):
            raise ValueError("Taxonomy cache checksum/source mismatch")
    else:
        if manifest.exists():
            raise ValueError("Taxonomy manifest exists without its data")
        response = httpx.get(URL, timeout=120)
        response.raise_for_status()
        if not isinstance(response.json(), list):
            raise ValueError("Unexpected taxonomy response")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        write_json(
            manifest,
            {
                "source_url": URL,
                "source_hash": sha256(path),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "snapshot_kind": "legacy_endpoint_current_snapshot_not_historical_version",
            },
        )
    print(path, sha256(path))


if __name__ == "__main__":
    main()
