"""Build the national CSV from the checked SKR metadata snapshots (no network)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "examples/registry_sources"


def build_rows() -> list[dict[str, str]]:
    websites = json.loads((SOURCES / "skr-websites.json").read_text(encoding="utf-8"))["entries"]
    codes = json.loads((SOURCES / "skr-codes.json").read_text(encoding="utf-8"))["entries"]
    # The two SKR pages use different dash styles for this municipality.
    aliases = {"Malung – Sälen": "Malung-Sälen"}
    by_name = {aliases.get(row["name"], row["name"]): row["code"] for row in codes}
    names = {row["name"] for row in websites}
    if len(websites) != 290 or len(codes) != 290 or len(names) != 290 or names != set(by_name):
        raise ValueError("SKR snapshots must contain matching sets of 290 unique municipalities")
    if len(set(by_name.values())) != 290:
        raise ValueError("Municipality codes must be unique")
    rows = []
    for item in websites:
        host = item["host"]
        domain = host.removeprefix("www.")
        domains = domain
        seeds = f"https://{host}/"
        # Preserve the previously supplied official Stockholm portal and its allowlist.
        if item["name"] == "Stockholm":
            domains += ";start.stockholm"
            seeds += ";https://start.stockholm/"
        rows.append({"id": by_name[item["name"]], "name": item["name"], "kind": "kommun",
                     "domains": domains, "seeds": seeds})
    return rows


def main():
    output = ROOT / "examples/municipalities.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "name", "kind", "domains", "seeds"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(build_rows())
    print("Wrote 290 municipalities to", output)


if __name__ == "__main__":
    main()
