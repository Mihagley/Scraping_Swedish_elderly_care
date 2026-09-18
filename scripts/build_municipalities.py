"""Build the national municipality input from SCB + SKR and official sites."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

SCB = "https://www.scb.se/hitta-statistik/regional-statistik-och-kartor/regionala-indelningar/lan-och-kommuner/lan-och-kommuner-i-kodnummerordning/"
SKR = "https://skr.se/kommunerochregioner/kommunerlista.8288.html"
ARCHIVE_WORDS = (
    "protokoll",
    "handlingar",
    "sammantrade",
    "sammanträde",
    "moten",
    "möten",
    "diarium",
    "anslagstavla",
    "kommunfullmaktige",
    "kommunfullmäktige",
    "namnd",
    "nämnd",
)
POLITICS_WORDS = ("kommun-och-politik", "politik", "demokrati", "organisation")


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in value if not unicodedata.combining(c)))


def same_official(host: str, official: str) -> bool:
    host, official = host.lower().removeprefix("www."), official.lower().removeprefix("www.")
    return host == official or host.endswith("." + official) or official.endswith("." + host)


def fetch(client: httpx.Client, url: str) -> httpx.Response:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    return response


def scb_codes(client: httpx.Client) -> dict[str, tuple[str, str]]:
    text = BeautifulSoup(fetch(client, SCB).text, "html.parser").get_text("\n", strip=True)
    found = {}
    for code, name in re.findall(r"(?m)^\s*(\d{4})\s+([^\n]+?)\s*$", text):
        name = re.sub(r"\s+", " ", name).strip()
        if re.fullmatch(r"\d{4}", code) and 1 < len(name) < 80:
            found[norm(name)] = (code, name)
    if len(found) != 290:
        raise RuntimeError(f"SCB parse produced {len(found)} municipalities, expected 290")
    return found


def skr_sites(client: httpx.Client) -> dict[str, tuple[str, str]]:
    soup = BeautifulSoup(fetch(client, SKR).text, "html.parser")
    found = {}
    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True).replace("Länk till annan webbplats.", "").strip()
        href = a.get("href", "")
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"} and parsed.hostname and label:
            found[norm(label)] = (label, href)
    return found


def archive_seeds(client: httpx.Client, home: str, official_domain: str) -> list[str]:
    queue = [(home, 0)]
    seen, archives = set(), []
    while queue and len(seen) < 35 and len(archives) < 5:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            response = fetch(client, url)
        except Exception:
            continue
        if "html" not in response.headers.get("content-type", "").lower():
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.select("a[href]"):
            target = urljoin(str(response.url), a["href"]).split("#", 1)[0]
            parsed = urlparse(target)
            if not parsed.hostname or not same_official(parsed.hostname, official_domain):
                continue
            label = (a.get_text(" ", strip=True) + " " + parsed.path).casefold()
            if any(word in label for word in ARCHIVE_WORDS):
                if target not in archives:
                    archives.append(target)
            elif depth == 0 and any(word in label for word in POLITICS_WORDS):
                queue.append((target, 1))
    return archives


def build(output: Path, validation: Path) -> None:
    headers = {
        "User-Agent": "MunicipalResearch/0.1 (+https://github.com/Mihagley/Scraping_Swedish_elderly_care)"
    }
    issues, rows = [], []
    with httpx.Client(timeout=25, headers=headers) as client:
        codes, sites = scb_codes(client), skr_sites(client)
        for key, (code, name) in sorted(codes.items(), key=lambda item: item[1][0]):
            if key not in sites:
                issues.append(f"missing_SKR_site:{code}:{name}")
                continue
            _, site = sites[key]
            try:
                response = fetch(client, site)
                home = str(response.url)
                domain = urlparse(home).hostname or ""
            except Exception as error:
                issues.append(f"official_site_failed:{code}:{name}:{error}")
                continue
            archives = archive_seeds(client, home, domain)
            if not archives:
                issues.append(f"meeting_archive_not_discovered:{code}:{name}")
            rows.append(
                {
                    "id": code,
                    "name": name,
                    "domains": domain,
                    "meeting_archives": ";".join(archives),
                    "seeds": ";".join(dict.fromkeys([home, *archives])),
                    "provenance": "SCB-2026+SKR+official-site-discovery",
                }
            )
    if len(rows) != 290:
        issues.append(f"row_count:{len(rows)}")
    if len({r["id"] for r in rows}) != len(rows):
        issues.append("duplicate_ids")
    if len({norm(r["name"]) for r in rows}) != len(rows):
        issues.append("duplicate_names")
    for row in rows:
        if not re.fullmatch(r"\d{4}", row["id"]):
            issues.append(f"bad_id:{row['id']}")
        if not row["domains"] or not row["seeds"]:
            issues.append(f"missing_domain_or_seed:{row['id']}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["id", "name", "domains", "meeting_archives", "seeds", "provenance"]
        )
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "rows": len(rows),
        "unique_ids": len({r["id"] for r in rows}),
        "with_meeting_archive": sum(bool(r["meeting_archives"]) for r in rows),
        "issues": issues,
        "sources": {"SCB": SCB, "SKR": SKR},
    }
    validation.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Domain/name/ID validation is mandatory. Missing archive discovery is surfaced as
    # a coverage gap and does not fabricate an archive URL; shards can still search it.
    fatal = [x for x in issues if not x.startswith("meeting_archive_not_discovered:")]
    if fatal:
        raise RuntimeError("Municipality input validation failed: " + "; ".join(fatal[:20]))


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    build(
        root / "research/municipalities-290.csv",
        root / "research/municipalities-290.validation.json",
    )
