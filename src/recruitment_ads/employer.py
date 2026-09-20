"""Municipal legal-entity identification, independent of workplace location."""

import csv
import re
import unicodedata
from datetime import date
from pathlib import Path


def normalize_name(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def normalize_orgnr(value):
    digits = re.sub(r"[\s-]", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("16"):
        digits = digits[2:]
    if not re.fullmatch(r"\d{10}", digits):
        return None
    total = sum(sum(divmod(int(d) * (2 if i % 2 == 0 else 1), 10)) for i, d in enumerate(digits))
    return digits if total % 10 == 0 else None


class EmployerMaster:
    def __init__(self, rows, aliases=()):
        self.rows = list(rows)
        self.by_org = {}
        self.by_municipality = {}
        for row in self.rows:
            org = normalize_orgnr(row["municipality_orgnr"])
            if not org or not re.fullmatch(r"\d{4}", row["municipality_id"]) or not row["source"]:
                raise ValueError("Invalid employer identifier or source")
            for field in ("valid_from", "valid_to"):
                if row.get(field):
                    date.fromisoformat(row[field])
            for other in self.by_org.get(org, []):
                if max(
                    row.get("valid_from") or "0001-01-01", other.get("valid_from") or "0001-01-01"
                ) <= min(
                    row.get("valid_to") or "9999-12-31", other.get("valid_to") or "9999-12-31"
                ):
                    raise ValueError(f"Overlapping organisation-number intervals: {org}")
            self.by_org.setdefault(org, []).append(row)
            self.by_municipality.setdefault(row["municipality_id"], []).append(row)
        self.aliases = {}
        for alias in aliases:
            if alias.get("validated") != "true" or not alias.get("source"):
                raise ValueError("Employer aliases require explicit validation and source")
            key = normalize_name(alias["employer_name"])
            if key in self.aliases:
                raise ValueError("Ambiguous employer-name alias")
            if alias["municipality_id"] not in {r["municipality_id"] for r in self.rows}:
                raise ValueError("Alias municipality is absent from master")
            self.aliases[key] = alias

    @classmethod
    def load(cls, path, aliases=None):
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        alias_rows = []
        if aliases:
            with Path(aliases).open(encoding="utf-8-sig", newline="") as f:
                alias_rows = list(csv.DictReader(f))
        return cls(rows, alias_rows)

    @staticmethod
    def active(row, when):
        return (not row.get("valid_from") or when >= row["valid_from"]) and (
            not row.get("valid_to") or when <= row["valid_to"]
        )

    def match(self, orgnr, name, when):
        unresolved = {
            "municipality_id": None,
            "municipality_name": None,
            "employer_match_method": "unresolved",
            "employer_validity_basis": None,
        }
        if not when:
            return unresolved
        # A present but unrecognised or malformed number must never be overridden by a name.
        if orgnr is not None and str(orgnr).strip():
            matches = [
                r for r in self.by_org.get(normalize_orgnr(orgnr), []) if self.active(r, when)
            ]
            method = "orgnr_exact"
        else:
            alias = self.aliases.get(normalize_name(name))
            matches = [
                r
                for r in self.by_municipality.get(alias["municipality_id"] if alias else None, [])
                if alias
                and self.active(alias, when)
                and r["municipality_id"] == alias["municipality_id"]
                and self.active(r, when)
            ]
            method = "employer_name_validated"
        if len(matches) != 1:
            return unresolved
        row = matches[0]
        return {
            "municipality_id": row["municipality_id"],
            "municipality_name": row["municipality_name"],
            "employer_match_method": method,
            "employer_validity_basis": row.get("validity_basis", "documented_interval"),
        }
