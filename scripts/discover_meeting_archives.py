from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "MunicipalResearch/0.1 (meeting-archive discovery; public research)"
STRONG = {
    "protokoll": 7,
    "sammanträde": 7,
    "sammantraden": 7,
    "sammanträden": 7,
    "kallelse": 5,
    "kallelser": 5,
    "möten": 5,
    "moten": 5,
    "handlingar": 4,
    "ärenden": 3,
    "arenden": 3,
    "kommunfullmäktige": 3,
    "kommunfullmaktige": 3,
    "nämnd": 2,
    "namnd": 2,
    "politisk": 2,
    "anslagstavla": 2,
    "diarium": 2,
}
NEGATIVE = {
    "press": -3,
    "nyheter": -2,
    "evenemang": -2,
    "kontakt": -2,
    "jobb": -3,
    "blankett": -2,
    "lov": -2,
}
CONTENT_MARKERS = [
    "protokoll",
    "sammanträde",
    "sammantraden",
    "sammanträden",
    "kallelse",
    "kallelser",
    "kommunfullmäktige",
    "kommunfullmaktige",
    "nämnd",
    "namnd",
    "möten",
    "moten",
]
VENDOR_HINTS = (
    "public360",
    "netpublicator",
    "meetings",
    "meetingplus",
    "ciceron",
    "sitevision",
    "quickchannel",
    "opengov",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_values(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(";") if x.strip()]


def normalize_host(host: str) -> str:
    return host.lower().strip(".").removeprefix("www.")


def host_allowed(url: str, official_domains: list[str]) -> bool:
    host = normalize_host(urlparse(url).hostname or "")
    return any(
        host == normalize_host(d) or host.endswith("." + normalize_host(d))
        for d in official_domains
    )


def token_score(text: str) -> int:
    lowered = text.lower()
    score = sum(weight for token, weight in STRONG.items() if token in lowered)
    score += sum(weight for token, weight in NEGATIVE.items() if token in lowered)
    return score


def content_marker_count(text: str) -> int:
    lowered = text.lower()
    return sum(1 for marker in CONTENT_MARKERS if marker in lowered)


def canonical_adjustment(url: str) -> int:
    lowered = url.lower()
    score = 0
    if re.search(r"20\\d{2}[-/]\\d{1,2}[-/]\\d{1,2}", lowered):
        score -= 18
    if re.search(r"/20\\d{2}(?:/|$)", lowered):
        score -= 8
    if "/nyheter/" in lowered or "nyhetsarkiv" in lowered or "driftstorning" in lowered:
        score -= 8
    if re.search(r"/details/\\d+", lowered):
        score -= 4
    if "anslagstavla" in lowered and not any(
        token in lowered for token in ("moten", "sammantr", "protokoll", "handlingar", "kallel")
    ):
        score -= 6
    if (
        any(token in lowered for token in ("moten", "sammantr"))
        and any(token in lowered for token in ("protokoll", "handlingar", "kallel"))
    ):
        score += 6
    if any(
        phrase in lowered
        for phrase in (
            "moten-handlingar-och-protokoll",
            "moten-och-protokoll",
            "sammantraden-och-protokoll",
            "sammantradeshandlingar",
            "kallelser-och-protokoll",
        )
    ):
        score += 5
    depth = len([part for part in urlparse(url).path.split("/") if part])
    if depth > 7:
        score -= min(depth - 7, 4)
    return score


def generic_archive_signal(text: str) -> bool:
    lowered = text.lower()
    has_meeting = any(token in lowered for token in ("möten", "moten", "sammanträ", "sammantra"))
    has_docs = any(token in lowered for token in ("protokoll", "handlingar", "kallel"))
    return has_meeting and has_docs


def is_over_specific(url: str) -> bool:
    lowered = url.lower()
    return bool(
        re.search(r"20\\d{2}[-/]\\d{1,2}[-/]\\d{1,2}", lowered)
        or "/nyheter/" in lowered
        or "nyhetsarkiv" in lowered
        or "driftstorning" in lowered
    )


def clean_url(base: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    url = urljoin(base, href)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed._replace(fragment="").geturl()


@dataclass
class Candidate:
    url: str
    score: int
    source: str
    anchor: str = ""
    status_code: int | None = None
    final_url: str | None = None
    content_markers: int = 0
    title: str = ""
    error: str | None = None
    accepted: bool = False


async def fetch(
    client: httpx.AsyncClient, url: str, retries: int = 2
) -> tuple[httpx.Response | None, str | None]:
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = await client.get(url, follow_redirects=True)
            if response.status_code < 500 or attempt == retries:
                return response, None
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.5 * (attempt + 1))
    return None, last_error


def links_from_html(
    html: str, base: str, official_domains: list[str], source: str
) -> list[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Candidate] = {}
    for link in soup.find_all("a", href=True):
        url = clean_url(base, link.get("href", ""))
        if not url:
            continue
        anchor = " ".join(link.stripped_strings)
        score = token_score(anchor + " " + url)
        external = not host_allowed(url, official_domains)
        vendor = any(hint in (urlparse(url).hostname or "").lower() for hint in VENDOR_HINTS)
        if external and not vendor and score < 5:
            continue
        if score <= 0:
            continue
        cand = Candidate(
            url=url,
            score=score + canonical_adjustment(url) + (2 if vendor else 0),
            source=source,
            anchor=anchor[:240],
        )
        old = out.get(url)
        if old is None or cand.score > old.score:
            out[url] = cand
    return list(out.values())


async def sitemap_candidates(
    client: httpx.AsyncClient,
    seed: str,
    official_domains: list[str],
    max_urls: int = 12000,
) -> tuple[list[Candidate], list[dict]]:
    parsed = urlparse(seed)
    root = f"{parsed.scheme}://{parsed.netloc}"
    queue = [urljoin(root, "/sitemap.xml")]
    diagnostics: list[dict] = []
    # Discover declared sitemaps too.
    robots_url = urljoin(root, "/robots.txt")
    robots, error = await fetch(client, robots_url, retries=1)
    diagnostics.append(
        {"url": robots_url, "status": robots.status_code if robots else None, "error": error}
    )
    if robots is not None and robots.status_code < 400:
        for line in robots.text.splitlines():
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm and sm not in queue:
                    queue.append(sm)

    seen_sitemaps: set[str] = set()
    found: dict[str, Candidate] = {}
    seen_urls = 0
    while queue and len(seen_sitemaps) < 8 and seen_urls < max_urls:
        sm = queue.pop(0)
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        response, error = await fetch(client, sm, retries=1)
        diagnostics.append(
            {"url": sm, "status": response.status_code if response else None, "error": error}
        )
        if response is None or response.status_code >= 400:
            continue
        try:
            root_xml = ET.fromstring(response.content)
        except ET.ParseError:
            continue
        locs = [
            elem.text.strip() for elem in root_xml.iter() if elem.tag.endswith("loc") and elem.text
        ]
        if root_xml.tag.endswith("sitemapindex"):
            for loc in locs[:50]:
                if host_allowed(loc, official_domains) and loc not in seen_sitemaps:
                    queue.append(loc)
            continue
        for loc in locs:
            seen_urls += 1
            if seen_urls > max_urls:
                break
            if not host_allowed(loc, official_domains):
                continue
            score = token_score(loc)
            if score >= 3:
                old = found.get(loc)
                cand = Candidate(
                    url=loc, score=score + canonical_adjustment(loc) + 1, source="sitemap"
                )
                if old is None or cand.score > old.score:
                    found[loc] = cand
    return list(found.values()), diagnostics


async def discover_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    row: dict[str, str],
    max_candidates: int,
    refresh: bool,
) -> dict:
    async with sem:
        mid = row["id"].strip()
        name = row["name"].strip()
        domains = split_values(row.get("domains"))
        seeds = split_values(row.get("seeds"))
        existing = split_values(row.get("meeting_archives"))
        result = {
            "id": mid,
            "name": name,
            "started_at": now(),
            "existing_archives": existing,
            "selected_archive": existing[0] if existing else None,
            "selection_status": "existing" if existing else "unresolved",
            "candidates": [],
            "sitemap_diagnostics": [],
            "errors": [],
        }
        if existing and not refresh:
            result["finished_at"] = now()
            return result
        if not seeds:
            result["finished_at"] = now()
            return result
        if refresh:
            result["selected_archive"] = None
            result["selection_status"] = "unresolved"

        pool: dict[str, Candidate] = {}
        seed = seeds[0]
        home, error = await fetch(client, seed)
        if home is None:
            result["errors"].append({"url": seed, "error": error})
        elif home.status_code < 400 and "html" in home.headers.get("content-type", "").lower():
            for cand in links_from_html(home.text, str(home.url), domains, "homepage"):
                pool[cand.url] = cand
        else:
            result["errors"].append({"url": seed, "status": home.status_code})

        sitemap, diagnostics = await sitemap_candidates(client, seed, domains)
        result["sitemap_diagnostics"] = diagnostics
        for cand in sitemap:
            old = pool.get(cand.url)
            if old is None or cand.score > old.score:
                pool[cand.url] = cand

        first_wave = sorted(pool.values(), key=lambda c: (-c.score, c.url))[:max_candidates]
        second_wave: dict[str, Candidate] = {}

        for cand in first_wave:
            response, error = await fetch(client, cand.url)
            cand.error = error
            if response is None:
                continue
            cand.status_code = response.status_code
            cand.final_url = str(response.url)
            if response.status_code >= 400:
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            cand.title = soup.title.get_text(" ", strip=True)[:240] if soup.title else ""
            visible = soup.get_text(" ", strip=True)[:120000]
            cand.content_markers = content_marker_count(visible)
            cand.score += min(cand.content_markers, 5)
            cand.score += min(max(token_score(cand.title), 0), 10)
            cand.score += canonical_adjustment(str(response.url))
            if host_allowed(str(response.url), domains):
                for child in links_from_html(
                    response.text, str(response.url), domains, f"follow:{cand.url}"
                ):
                    child.score += 1
                    old = second_wave.get(child.url)
                    if old is None or child.score > old.score:
                        second_wave[child.url] = child

        # Probe a small number of promising links discovered one level deeper,
        # especially external archive vendors linked from an official meeting page.
        for cand in sorted(second_wave.values(), key=lambda c: (-c.score, c.url))[:8]:
            if cand.url in pool:
                continue
            response, error = await fetch(client, cand.url)
            cand.error = error
            if response is not None:
                cand.status_code = response.status_code
                cand.final_url = str(response.url)
                if (
                    response.status_code < 400
                    and "html" in response.headers.get("content-type", "").lower()
                ):
                    soup = BeautifulSoup(response.text, "html.parser")
                    cand.title = soup.title.get_text(" ", strip=True)[:240] if soup.title else ""
                    visible = soup.get_text(" ", strip=True)[:120000]
                    cand.content_markers = content_marker_count(visible)
                    cand.score += min(cand.content_markers, 5)
                    cand.score += min(max(token_score(cand.title), 0), 10)
                    cand.score += canonical_adjustment(str(response.url))
            pool[cand.url] = cand

        ranked = sorted(pool.values(), key=lambda c: (-c.score, c.url))
        for cand in ranked:
            final = cand.final_url or cand.url
            ok_status = cand.status_code is not None and cand.status_code < 400
            archive_evidence = cand.content_markers >= 2
            descriptive_text = cand.anchor + " " + cand.url + " " + cand.title
            strong_link = token_score(descriptive_text) >= 5
            vendor = any(hint in (urlparse(final).hostname or "").lower() for hint in VENDOR_HINTS)
            generic = generic_archive_signal(descriptive_text)
            cand.accepted = bool(
                ok_status
                and not is_over_specific(final)
                and strong_link
                and cand.score >= 10
                and (archive_evidence or vendor)
                and (generic or vendor)
            )

        accepted = [c for c in ranked if c.accepted]
        if accepted:
            best = accepted[0]
            result["selected_archive"] = best.final_url or best.url
            result["selection_status"] = "confident"
        elif ranked:
            result["selection_status"] = "review"

        result["candidates"] = [asdict(c) for c in ranked[:15]]
        result["finished_at"] = now()
        return result


async def run(
    rows: list[dict[str, str]], concurrency: int, max_candidates: int, refresh: bool
) -> list[dict]:
    timeout = httpx.Timeout(20.0, connect=10.0)
    limits = httpx.Limits(
        max_connections=max(concurrency * 2, 20), max_keepalive_connections=concurrency
    )
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=timeout, limits=limits, headers=headers, follow_redirects=True
    ) as client:
        tasks = [discover_one(client, sem, row, max_candidates, refresh) for row in rows]
        return await asyncio.gather(*tasks)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover municipality meeting/protocol archive landing pages"
    )
    parser.add_argument("--registry", type=Path, default=Path("research/municipalities.csv"))
    parser.add_argument(
        "--audit", type=Path, default=Path("research/meeting-archive-discovery.json")
    )
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--max-candidates", type=int, default=8)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-evaluate and replace existing archive selections using the current quality gate.",
    )
    args = parser.parse_args()

    with args.registry.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    required = {"id", "name", "domains", "meeting_archives", "seeds"}
    if not required.issubset(fields):
        raise SystemExit(f"Registry lacks required columns: {sorted(required - set(fields))}")

    results = asyncio.run(run(rows, args.concurrency, args.max_candidates, args.refresh))
    selected = {
        item["id"]: item["selected_archive"] for item in results if item["selected_archive"]
    }
    for row in rows:
        if args.refresh:
            row["meeting_archives"] = selected.get(row["id"], "")
        elif not split_values(row.get("meeting_archives")) and selected.get(row["id"]):
            row["meeting_archives"] = selected[row["id"]]

    with args.registry.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "generated_at": now(),
        "registry": args.registry.as_posix(),
        "registry_sha256": hashlib.sha256(args.registry.read_bytes()).hexdigest(),
        "rows": len(rows),
        "existing": sum(item["selection_status"] == "existing" for item in results),
        "confident": sum(item["selection_status"] == "confident" for item in results),
        "review": sum(item["selection_status"] == "review" for item in results),
        "unresolved": sum(item["selection_status"] == "unresolved" for item in results),
        "archives_populated": sum(bool(split_values(row.get("meeting_archives"))) for row in rows),
    }
    payload = {"schema_version": 1, "summary": summary, "municipalities": results}
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
