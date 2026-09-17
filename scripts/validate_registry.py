from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

SCB_URL = (
    "https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/"
    "regionala-indelningar/lan-och-kommuner/kommuner-i-bokstavsordning/"
)
SKR_URL = "https://skr.se/kommunerochregioner/kommunerlista.8288.html"
COUNTY_PREFIXES = {
    "01",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "12",
    "13",
    "14",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
}
LANGUAGE_TERMS = (
    "språkkrav",
    "språktest",
    "språkbedöm",
    "svenska språket",
    "svenskakunsk",
    "kunskaper i svenska",
    "språknivå",
)
USER_AGENT = (
    "MunicipalRegistryValidator/1.0 "
    "(+https://github.com/Mihagley/Scraping_Swedish_elderly_care)"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s*-\s*", "-", value)
    return re.sub(r"\s+", " ", value)


def split_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def host_of(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    return (parsed.hostname or "").casefold().rstrip(".")


def host_core(value: str) -> str:
    host = host_of(value)
    return host[4:] if host.startswith("www.") else host


def domain_covers(candidate: str, expected: str) -> bool:
    candidate_core = host_core(candidate)
    expected_core = host_core(expected)
    if not candidate_core or not expected_core:
        return False
    return (
        candidate_core == expected_core
        or expected_core.endswith("." + candidate_core)
        or candidate_core.endswith("." + expected_core)
    )


def parse_scb(html: bytes) -> dict[str, str]:
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    municipalities: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(.+?)\s+(\d{4})", line.strip())
        if not match:
            continue
        name, municipality_id = match.group(1).strip(), match.group(2)
        if municipality_id[:2] not in COUNTY_PREFIXES:
            continue
        if municipality_id in municipalities and municipalities[municipality_id] != name:
            raise ValueError(f"SCB duplicate code with different names: {municipality_id}")
        municipalities[municipality_id] = name
    if len(municipalities) != 290:
        raise ValueError(f"SCB parsing produced {len(municipalities)} municipalities, expected 290")
    return municipalities


def parse_skr(html: bytes, scb: dict[str, str]) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    by_name = {normalize_name(name): municipality_id for municipality_id, name in scb.items()}
    result: dict[str, dict[str, str]] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href.startswith(("http://", "https://")):
            continue
        label = anchor.get_text(" ", strip=True)
        label = re.sub(r"\s+Länk till annan webbplats\.?\s*$", "", label, flags=re.IGNORECASE)
        municipality_id = by_name.get(normalize_name(label))
        if not municipality_id:
            continue
        host = host_of(href)
        if not host or host.endswith("skr.se"):
            continue
        previous = result.get(municipality_id)
        record = {"url": href, "host": host}
        if previous and previous != record:
            raise ValueError(f"SKR contains multiple official links for {municipality_id}")
        result[municipality_id] = record
    if len(result) != 290:
        missing = sorted(set(scb) - set(result))
        raise ValueError(
            f"SKR parsing produced {len(result)} municipal domains, expected 290; missing {missing}"
        )
    return result


def load_registry(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def value_urls(row: dict[str, str], field: str) -> list[str]:
    return split_values(row.get(field))


def suspicious_error_page(text: str) -> bool:
    head = text[:5000].casefold()
    return any(
        marker in head
        for marker in (
            "404 - sidan kunde inte hittas",
            "404 sidan kunde inte hittas",
            "sidan finns inte",
            "page not found",
            "sidan saknas",
        )
    )


async def probe_one(client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore) -> dict:
    result = {
        "url": url,
        "status": "unreachable",
        "status_code": None,
        "final_url": None,
        "content_type": None,
        "error": None,
        "suspected_error_page": False,
        "language_relevance": None,
    }
    async with semaphore:
        try:
            async with client.stream("GET", url, follow_redirects=True) as response:
                result["status_code"] = response.status_code
                result["final_url"] = str(response.url)
                result["content_type"] = response.headers.get("content-type", "")
                result["status"] = "reachable" if response.status_code < 500 else "server_error"
                collected = bytearray()
                async for chunk in response.aiter_bytes():
                    collected.extend(chunk)
                    if len(collected) >= 196_608:
                        break
                content_type = result["content_type"].casefold()
                if "html" in content_type or "text" in content_type:
                    text = bytes(collected).decode(response.encoding or "utf-8", errors="replace")
                    result["suspected_error_page"] = suspicious_error_page(text)
                    folded = BeautifulSoup(text, "html.parser").get_text(" ", strip=True).casefold()
                    result["language_relevance"] = any(term in folded for term in LANGUAGE_TERMS)
        except Exception as error:  # network evidence belongs in the report, not a crash
            result["error"] = f"{type(error).__name__}: {error}"
    return result


async def probe_urls(urls: list[str], concurrency: int) -> dict[str, dict]:
    unique = list(dict.fromkeys(url for url in urls if url))
    timeout = httpx.Timeout(15.0, connect=10.0)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.5"}
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers) as client:
        results = await asyncio.gather(*(probe_one(client, url, semaphore) for url in unique))
    return {item["url"]: item for item in results}


def issue(code: str, municipality_id: str | None, field: str, detail: str) -> dict:
    return {
        "code": code,
        "municipality_id": municipality_id,
        "field": field,
        "detail": detail,
    }


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# National municipality registry validation",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Overall status: **{report['status']}**",
        f"Matrix safe: **{str(report['matrix_safe']).lower()}**",
        "",
        "Canonical municipality IDs/names are checked against SCB 2026; official municipal web domains are checked against SKR's municipality list.",
        "Meeting archives and research seeds are registry data and are reported as missing or invalid when absent/unusable; no research matrix was launched.",
        "",
        "## Summary",
        "",
        f"- Expected municipalities: {summary['expected_municipalities']}",
        f"- Registry rows: {summary['registry_rows']}",
        f"- Municipalities with issues: {summary['municipalities_with_issues']}",
        f"- Total issues: {summary['total_issues']}",
        f"- Missing registry entries: {summary['missing_registry_entries']}",
        f"- Invalid/mismatched IDs: {summary['invalid_ids']}",
        f"- Missing/invalid official domains: {summary['domain_issues']}",
        f"- Missing/invalid meeting archives: {summary['meeting_archive_issues']}",
        f"- Missing/invalid seeds: {summary['seed_issues']}",
        "",
        "## Municipality-level results",
        "",
        "| ID | Municipality | Expected official domain | ID | Domain | Meeting archive | Seed |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in report["municipalities"]:
        lines.append(
            "| {id} | {name} | `{domain}` | {id_status} | {domain_status} | {archive_status} | {seed_status} |".format(
                id=item["id"],
                name=item["name"].replace("|", "\\|"),
                domain=item["expected_official_domain"],
                id_status=item["id_status"],
                domain_status=item["official_domain_status"],
                archive_status=item["meeting_archive_status"],
                seed_status=item["seed_status"],
            )
        )
    lines += [
        "",
        "The JSON report contains every issue, source hash, HTTP probe result, redirect target, and row-level validation detail.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the national municipality registry")
    parser.add_argument("--registry", type=Path, default=Path("research/municipalities.csv"))
    parser.add_argument(
        "--json-report", type=Path, default=Path("research/registry-validation.json")
    )
    parser.add_argument(
        "--markdown-report", type=Path, default=Path("research/REGISTRY_VALIDATION.md")
    )
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--fail-on-invalid", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 50:
        parser.error("--concurrency must be between 1 and 50")

    with httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        scb_response = client.get(SCB_URL)
        scb_response.raise_for_status()
        skr_response = client.get(SKR_URL)
        skr_response.raise_for_status()
    scb_bytes, skr_bytes = scb_response.content, skr_response.content
    scb = parse_scb(scb_bytes)
    skr = parse_skr(skr_bytes, scb)

    rows, columns = load_registry(args.registry)
    id_counts = Counter((row.get("id") or "").strip() for row in rows if (row.get("id") or "").strip())
    rows_by_id = {
        (row.get("id") or "").strip(): row
        for row in rows
        if (row.get("id") or "").strip() and id_counts[(row.get("id") or "").strip()] == 1
    }

    urls_to_probe = [record["url"] for record in skr.values()]
    for row in rows:
        urls_to_probe.extend(value_urls(row, "meeting_archives"))
        urls_to_probe.extend(value_urls(row, "seeds"))
    probes = asyncio.run(probe_urls(urls_to_probe, args.concurrency))

    issues: list[dict] = []
    municipality_results: list[dict] = []
    required_columns = {"id", "name", "domains", "meeting_archives", "seeds"}
    missing_columns = sorted(required_columns - set(columns)) if rows else sorted(required_columns)
    for column in missing_columns:
        issues.append(issue("missing_column", None, column, f"Registry is missing column {column}"))

    for municipality_id, name in sorted(scb.items()):
        expected = skr[municipality_id]
        official_probe = probes[expected["url"]]
        row = rows_by_id.get(municipality_id)
        item_issues: list[str] = []
        if row is None:
            id_status = "missing"
            official_domain_status = "missing"
            meeting_archive_status = "missing"
            seed_status = "missing"
            for code, field in (
                ("missing_registry_entry", "id"),
                ("missing_official_domain", "domains"),
                ("missing_meeting_archive", "meeting_archives"),
                ("missing_seed", "seeds"),
            ):
                detail = f"{name} ({municipality_id}) is absent from the national registry"
                issues.append(issue(code, municipality_id, field, detail))
                item_issues.append(code)
            registry_name = None
            registry_domains: list[str] = []
            archives: list[str] = []
            seeds: list[str] = []
        else:
            registry_name = (row.get("name") or "").strip()
            registry_domains = split_values(row.get("domains"))
            archives = value_urls(row, "meeting_archives")
            seeds = value_urls(row, "seeds")
            id_status = "valid"
            if normalize_name(registry_name) != normalize_name(name):
                id_status = "name_mismatch"
                code = "municipality_name_mismatch"
                issues.append(
                    issue(
                        code,
                        municipality_id,
                        "name",
                        f"Registry name {registry_name!r} does not match SCB name {name!r}",
                    )
                )
                item_issues.append(code)

            if not registry_domains:
                official_domain_status = "missing"
                code = "missing_official_domain"
                issues.append(issue(code, municipality_id, "domains", "No official domain supplied"))
                item_issues.append(code)
            elif not any(domain_covers(domain, expected["host"]) for domain in registry_domains):
                official_domain_status = "mismatch"
                code = "official_domain_mismatch"
                issues.append(
                    issue(
                        code,
                        municipality_id,
                        "domains",
                        f"Registry domains {registry_domains!r} do not cover SKR domain {expected['host']}",
                    )
                )
                item_issues.append(code)
            else:
                official_domain_status = "valid"

            if not archives:
                meeting_archive_status = "missing"
                code = "missing_meeting_archive"
                issues.append(
                    issue(code, municipality_id, "meeting_archives", "No meeting archive supplied")
                )
                item_issues.append(code)
            else:
                bad_archives = [
                    url
                    for url in archives
                    if probes.get(url, {}).get("status") != "reachable"
                    or probes.get(url, {}).get("suspected_error_page")
                ]
                meeting_archive_status = "invalid" if bad_archives else "reachable"
                if bad_archives:
                    code = "invalid_meeting_archive"
                    issues.append(
                        issue(
                            code,
                            municipality_id,
                            "meeting_archives",
                            "Unreachable/error-page archive URLs: " + "; ".join(bad_archives),
                        )
                    )
                    item_issues.append(code)

            if not seeds:
                seed_status = "missing"
                code = "missing_seed"
                issues.append(issue(code, municipality_id, "seeds", "No research seed supplied"))
                item_issues.append(code)
            else:
                bad_seeds = [
                    url
                    for url in seeds
                    if probes.get(url, {}).get("status") != "reachable"
                    or probes.get(url, {}).get("suspected_error_page")
                ]
                seed_status = "invalid" if bad_seeds else "reachable"
                if bad_seeds:
                    code = "invalid_seed"
                    issues.append(
                        issue(
                            code,
                            municipality_id,
                            "seeds",
                            "Unreachable/error-page seed URLs: " + "; ".join(bad_seeds),
                        )
                    )
                    item_issues.append(code)

        if official_probe["status"] != "reachable":
            code = "skr_official_domain_unreachable"
            issues.append(
                issue(
                    code,
                    municipality_id,
                    "domains",
                    f"SKR-listed official URL could not be reached: {expected['url']}",
                )
            )
            item_issues.append(code)

        municipality_results.append(
            {
                "id": municipality_id,
                "name": name,
                "expected_official_url": expected["url"],
                "expected_official_domain": expected["host"],
                "official_domain_probe": official_probe,
                "registry_present": row is not None,
                "registry_name": registry_name,
                "registry_domains": registry_domains,
                "meeting_archives": archives,
                "seeds": seeds,
                "id_status": id_status,
                "official_domain_status": official_domain_status,
                "meeting_archive_status": meeting_archive_status,
                "seed_status": seed_status,
                "issues": sorted(set(item_issues)),
            }
        )

    expected_ids = set(scb)
    for row in rows:
        municipality_id = (row.get("id") or "").strip()
        if not re.fullmatch(r"\d{4}", municipality_id):
            issues.append(
                issue("invalid_municipality_id_format", municipality_id or None, "id", "ID must be four digits")
            )
        elif municipality_id not in expected_ids:
            issues.append(
                issue(
                    "unknown_municipality_id",
                    municipality_id,
                    "id",
                    "ID is not present in SCB's 2026 municipality list",
                )
            )
        if municipality_id and id_counts[municipality_id] > 1:
            issues.append(
                issue(
                    "duplicate_municipality_id",
                    municipality_id,
                    "id",
                    f"ID occurs {id_counts[municipality_id]} times",
                )
            )

    issue_counts = Counter(item["code"] for item in issues)
    municipalities_with_issues = sum(bool(item["issues"]) for item in municipality_results)
    invalid_id_codes = {
        "missing_registry_entry",
        "invalid_municipality_id_format",
        "unknown_municipality_id",
        "duplicate_municipality_id",
        "municipality_name_mismatch",
    }
    domain_codes = {
        "missing_official_domain",
        "official_domain_mismatch",
        "skr_official_domain_unreachable",
    }
    archive_codes = {"missing_meeting_archive", "invalid_meeting_archive"}
    seed_codes = {"missing_seed", "invalid_seed"}
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "valid" if not issues else "invalid",
        "matrix_safe": not issues and len(rows) == 290,
        "registry": {
            "path": args.registry.as_posix(),
            "exists": args.registry.is_file(),
            "columns": columns,
            "missing_required_columns": missing_columns,
        },
        "canonical_sources": {
            "scb": {
                "url": SCB_URL,
                "sha256": sha256(scb_bytes),
                "municipalities": len(scb),
            },
            "skr": {
                "url": SKR_URL,
                "sha256": sha256(skr_bytes),
                "official_domains": len(skr),
            },
        },
        "summary": {
            "expected_municipalities": 290,
            "registry_rows": len(rows),
            "municipalities_with_issues": municipalities_with_issues,
            "total_issues": len(issues),
            "missing_registry_entries": issue_counts["missing_registry_entry"],
            "invalid_ids": sum(issue_counts[code] for code in invalid_id_codes),
            "domain_issues": sum(issue_counts[code] for code in domain_codes),
            "meeting_archive_issues": sum(issue_counts[code] for code in archive_codes),
            "seed_issues": sum(issue_counts[code] for code in seed_codes),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "issues": issues,
        "municipalities": municipality_results,
        "url_probes": probes,
    }
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_report.write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 2 if args.fail_on_invalid and not report["matrix_safe"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
