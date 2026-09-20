"""Rebuild a current 290-municipality crosswalk from two official local snapshots."""

import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from openpyxl import load_workbook

from recruitment_ads.download import sha256, write_json
from recruitment_ads.employer import EmployerMaster, normalize_name, normalize_orgnr

SCB = "https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/"
REGISTER = "https://api.skolverket.se/skolenhetsregistret/export/skolenhet/adressfil"


def build(scb, register, output):
    text = BeautifulSoup(Path(scb).read_text(encoding="utf-8-sig"), "html.parser").get_text(
        " ", strip=True
    )
    # SCB rows are explicitly coded; no school workplace is used to assign employers.
    soup = BeautifulSoup(Path(scb).read_text(encoding="utf-8-sig"), "html.parser")
    municipalities = {}
    for line in soup.get_text("\n", strip=True).splitlines():
        match = re.fullmatch(r"(\d{4})\s+([A-Za-zÅÄÖåäöÉé -]+)", line.strip())
        if match:
            municipalities[match[1]] = match[2]
    if len(municipalities) != 290:
        raise ValueError(
            f"SCB parser found {len(municipalities)}, expected 290; inspect source ({len(text)} chars)"
        )
    wb = load_workbook(register, read_only=True)
    values = iter(wb["Huvudmän"].values)
    headers = next(values)
    entities = [dict(zip(headers, row, strict=True)) for row in values]
    municipal = [r for r in entities if r["JURIDISK FORM"] == "Kommuner"]
    rows = []
    aliases = []
    for code, name in sorted(municipalities.items()):
        # Swedish genitive 's' is deterministic; ambiguous joins stop instead of guessing.
        stems = {normalize_name(name), normalize_name(name + "s")}
        if code == "2080":
            stems.add("falu")  # SCB Falun = the register's municipal legal entity FALU KOMMUN.
        matches = [
            r
            for r in municipal
            if normalize_name(
                re.sub(r"\s+(kommun|stad)$", "", r["NAMN"], flags=re.I).removeprefix("REGION ")
            )
            in stems
        ]
        if len(matches) != 1:
            raise ValueError(f"Register join unresolved: {code} {name}: {len(matches)}")
        row = matches[0]
        org = normalize_orgnr(row["ORGANISATIONSNR"])
        rows.append(
            {
                "municipality_id": code,
                "municipality_name": name,
                "municipality_orgnr": org,
                "valid_from": "",
                "valid_to": "",
                "source": REGISTER + " ; " + SCB,
                "validity_basis": "current_snapshot_historical_continuity_unverified",
            }
        )
        aliases.append(
            {
                "employer_name": row["NAMN"],
                "municipality_id": code,
                "valid_from": "",
                "valid_to": "",
                "validated": "true",
                "source": REGISTER,
            }
        )
    EmployerMaster(rows, aliases)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    for dest, records in ((output, rows), (output.with_name("employer_aliases.csv"), aliases)):
        if dest.exists():
            raise FileExistsError(f"Refusing to replace register snapshot: {dest}")
        with dest.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    write_json(
        output.with_suffix(".sources.json"),
        {
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "n_municipalities": len(rows),
            "sources": [
                {"url": SCB, "sha256": sha256(scb)},
                {"url": REGISTER, "sha256": sha256(register)},
            ],
            "historical_validity": "Unknown endpoints remain blank. Applying current legal entities to historical recruitment assumes continuity; exact-number matching does not validate historical municipal boundaries.",
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scb-html", required=True)
    p.add_argument("--register-xlsx", required=True)
    p.add_argument("--output", default="research/recruitment_ads/employer_master.csv")
    args = p.parse_args()
    build(args.scb_html, args.register_xlsx, args.output)
