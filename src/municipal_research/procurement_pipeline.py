"""Automated fetch -> download -> extract -> classify pipeline for procurement documents.

Two sources are supported:

* TED (EU Tenders Electronic Daily) search API v3. Open, no key, but only covers
  procurements above the EU thresholds. Notice descriptions are classified directly;
  linked procurement documents are downloaded only when they are publicly reachable.
* Hitta LOV-uppdrag (Upphandlingsmyndigheten, formerly Valfrihetswebben). The national
  LOV advertising platform. Each advert usually links to the municipality's own page with
  the förfrågningsunderlag; the pipeline follows that link one hop and downloads PDFs.

Everything goes through ``network.Fetcher`` (robots.txt, pacing, retries, HTTP cache,
audit log) except the TED search itself, which is a POST and is cached separately.
Re-running the same command reuses the caches, so an interrupted run resumes cheaply.
A document that cannot be reached is recorded as ``missing_document``, never as a zero.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .extraction import clean_text
from .network import Fetcher, FetchError, Pacer, normalize_url
from .storage import Audit, atomic_bytes, digest, read_json, write_json

TED_SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
TED_NOTICE_URL = "https://ted.europa.eu/sv/notice/-/detail/{number}"
# 85311100 welfare services for the elderly; 85311000 welfare services involving
# accommodation; 85312100 daytime care; 85144100 nursing home services.
TED_ELDERLY_CPV = ("85311100", "85311000", "85312100", "85144100")
TED_FIELDS = (
    "publication-number",
    "publication-date",
    "notice-title",
    "buyer-name",
    "buyer-city",
    "classification-cpv",
    "description-lot",
    "description-proc",
    "title-lot",
    "document-url-lot",
    "links",
)

LOV_HOST = "www.upphandlingsmyndigheten.se"
LOV_LIST_URL = "https://www.upphandlingsmyndigheten.se/hitta-lov-uppdrag/"
LOV_ADVERT_PATH = "/hitta-lov-uppdrag/annonser/"
_DOC_LINK_TEXT = re.compile(
    r"förfrågningsunderlag|upphandlingsdokument|ansökningsunderlag|villkor|kravspecifikation|"
    r"avtal|uppdragsbeskrivning|ansökan|leverantör|valfrihetssystem|\blov\b",
    re.IGNORECASE,
)


def ted_default_query(from_year: int, to_year: int) -> str:
    cpv = " ".join(TED_ELDERLY_CPV)
    return (
        f"classification-cpv IN ({cpv}) AND buyer-country IN (SWE) "
        f"AND publication-date>={from_year}0101 AND publication-date<={to_year}1231"
    )


# --------------------------------------------------------------------------- TED


def _flatten(value: Any) -> str:
    """TED returns strings, lists, or language maps such as {"swe": [...], "eng": [...]}."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " | ".join(v for v in (_flatten(item) for item in value) if v)
    if isinstance(value, dict):
        for key in ("swe", "sv", "SWE", "eng", "en", "ENG"):
            if key in value:
                return _flatten(value[key])
        return " | ".join(v for v in (_flatten(item) for item in value.values()) if v)
    return str(value)


def _urls(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found += re.findall(r"https?://[^\s\"'<>|]+", value)
    elif isinstance(value, list):
        for item in value:
            found += _urls(item)
    elif isinstance(value, dict):
        for item in value.values():
            found += _urls(item)
    return list(dict.fromkeys(found))


class TedClient:
    """Iterates TED search results with iteration tokens; each page is cached on disk."""

    def __init__(
        self,
        cache: Path,
        audit: Audit,
        *,
        client: httpx.Client | None = None,
        interval: float = 1.0,
        offline: bool = False,
        user_agent: str = "",
    ):
        self.cache, self.audit, self.offline = cache / "ted", audit, offline
        self.client = client or httpx.Client(
            timeout=60, headers={"User-Agent": user_agent} if user_agent else None
        )
        self.pacer = Pacer(interval)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        key = digest(json.dumps(body, sort_keys=True))
        path = self.cache / f"{key}.json"
        if path.exists():
            self.audit.emit("ted_search", cache_hit=True, key=key)
            return read_json(path)
        if self.offline:
            raise FetchError("Offline TED cache miss")
        for attempt in range(4):
            self.pacer.wait("ted")
            try:
                response = self.client.post(TED_SEARCH_URL, json=body)
            except httpx.TransportError as error:
                if attempt == 3:
                    raise FetchError(f"TED transport error: {type(error).__name__}") from error
                self.audit.emit("ted_retry", error=type(error).__name__)
                self.pacer.sleep(min(2 ** (attempt + 2), 30))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                self.audit.emit("ted_retry", status=response.status_code)
                self.pacer.sleep(min(2 ** (attempt + 2), 30))
                continue
            if response.status_code >= 400:
                raise FetchError(f"TED HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            write_json(path, payload)
            self.audit.emit("ted_search", cache_hit=False, key=key, n=len(payload.get("notices", [])))
            return payload
        raise FetchError("TED retries exhausted")

    def search(
        self, query: str, fields: Iterable[str] = TED_FIELDS, *, limit: int = 100, max_pages: int = 200
    ) -> Iterator[dict[str, Any]]:
        token: str | None = None
        for _ in range(max_pages):
            body: dict[str, Any] = {
                "query": query,
                "fields": list(fields),
                "limit": limit,
                "scope": "ALL",
                "paginationMode": "ITERATION",
            }
            if token:
                body["iterationNextToken"] = token
            payload = self._post(body)
            notices = payload.get("notices") or []
            yield from notices
            token = payload.get("iterationNextToken")
            if not notices or not token:
                return


def ted_notice_to_row(notice: dict[str, Any]) -> dict[str, Any]:
    number = _flatten(notice.get("publication-number"))
    description = "\n".join(
        part
        for part in (
            _flatten(notice.get("notice-title")),
            _flatten(notice.get("title-lot")),
            _flatten(notice.get("description-proc")),
            _flatten(notice.get("description-lot")),
        )
        if part
    )
    doc_urls = _urls(notice.get("document-url-lot"))
    links = notice.get("links") or {}
    html_direct = links.get("htmlDirect") or {}
    notice_html = html_direct.get("SWE") or html_direct.get("ENG") or (
        f"https://ted.europa.eu/sv/notice/{number}/html" if number else ""
    )
    return {
        "notice_id": number,
        "publication_date": _flatten(notice.get("publication-date"))[:10],
        "buyer_name": _flatten(notice.get("buyer-name")),
        "municipality_name": _flatten(notice.get("buyer-city")),
        "title": _flatten(notice.get("notice-title")),
        "cpv": _flatten(notice.get("classification-cpv")),
        "description": description,
        "document_urls": doc_urls,
        "notice_html_url": notice_html,
        "source_url": TED_NOTICE_URL.format(number=number) if number else "",
        "coverage_status": "ted_above_eu_threshold",
    }


_NOT_DOCUMENT_HOSTS = ("ted.europa.eu", "europa.eu", "w3.org", "eur-lex")


def document_urls_in_text(text: str) -> list[str]:
    """Procurement-document links mentioned in a notice (e.g. e-Avrop, Tendsign, Opic)."""
    urls = []
    for raw in re.findall(r"(?:https?://|www\.)[^\s<>\"'()]+", text):
        url = raw.rstrip(".,;:)]")
        if url.startswith("www."):
            url = "https://" + url
        host = urlsplit(url).hostname or ""
        if host and not any(bad in host for bad in _NOT_DOCUMENT_HOSTS) and "@" not in url:
            urls.append(url)
    return list(dict.fromkeys(urls))


# --------------------------------------------------------------- Hitta LOV-uppdrag


def parse_lov_listing(html: bytes | str, base: str = LOV_LIST_URL) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base, anchor["href"])
        parts = urlsplit(url)
        if parts.hostname and parts.hostname.endswith("upphandlingsmyndigheten.se"):
            path = parts.path if parts.path.endswith("/") else parts.path + "/"
            # /hitta-lov-uppdrag/annonser/<buyer>/<system>/
            if path.startswith(LOV_ADVERT_PATH) and path.count("/") >= 5:
                found.append(f"https://{LOV_HOST}{path}")
    return list(dict.fromkeys(found))


@dataclass
class LovAdvert:
    url: str
    title: str = ""
    buyer_name: str = ""
    service_area: str = ""
    start_date: str = ""
    updated: str = ""
    reference: str = ""
    document_links: list[str] = field(default_factory=list)
    text: str = ""


def _label_value(soup: BeautifulSoup, *labels: str) -> str:
    pattern = re.compile(r"^\s*(?:" + "|".join(labels) + r")\s*:?\s*$", re.IGNORECASE)
    for node in soup.find_all(string=pattern):
        element = node.parent
        # <dt>Label</dt><dd>Value</dd>, <th>/<td>, or <strong>Label</strong> Value
        sibling = element.find_next_sibling()
        if sibling is not None and sibling.get_text(strip=True):
            return sibling.get_text(" ", strip=True)
        tail = element.next_sibling
        if isinstance(tail, str) and tail.strip():
            return tail.strip(" :\n\t")
    return ""


def parse_lov_advert(html: bytes | str, url: str) -> LovAdvert:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    slug_parts = urlsplit(url).path.strip("/").split("/")
    buyer_slug = slug_parts[2] if len(slug_parts) > 2 else ""
    buyer = " ".join(word.capitalize() for word in buyer_slug.split("-")).replace(" Kommun", " kommun")
    main = soup.find("main") or soup.body or soup
    links = []
    for anchor in main.find_all("a", href=True):
        target = urljoin(url, anchor["href"])
        host = urlsplit(target).hostname or ""
        if not target.startswith(("http://", "https://")) or "upphandlingsmyndigheten.se" in host:
            continue
        text = anchor.get_text(" ", strip=True) + " " + target
        if _DOC_LINK_TEXT.search(text) or target.lower().split("?")[0].endswith(".pdf"):
            links.append(target)
    return LovAdvert(
        url=url,
        title=title,
        buyer_name=_label_value(soup, "upphandlande myndighet", "myndighet", "kommun", "region")
        or buyer,
        service_area=_label_value(soup, "tjänsteområde", "tjänstekategori", "område", "kategori"),
        start_date=_label_value(soup, "startdatum", "start", "gäller från"),
        updated=_label_value(soup, "senast uppdaterad", "uppdaterad"),
        reference=_label_value(soup, "diarienummer", "referensnummer", "dnr"),
        document_links=list(dict.fromkeys(links)),
        text=clean_text(main.get_text("\n", strip=True)),
    )


def discover_lov_adverts(
    fetcher: Fetcher, audit: Audit, *, max_pages: int = 60, seeds: Iterable[str] = ()
) -> list[str]:
    """Collect advert URLs from the listing page and its paginated variants, plus seeds.

    The listing uses a "Visa fler" button. Common server-side variants (?page=N) are
    tried until a page adds no new adverts, so the crawl stops on its own.
    """
    adverts: list[str] = [normalize_url(s) for s in seeds]
    try:
        first = fetcher.get(LOV_LIST_URL, [LOV_HOST])
        adverts += parse_lov_listing(first.body)
    except FetchError as error:
        audit.emit("lov_listing_error", url=LOV_LIST_URL, error=str(error))
        return list(dict.fromkeys(adverts))
    for param in ("page", "p", "sida"):
        added_any = False
        for page in range(2, max_pages + 1):
            url = f"{LOV_LIST_URL}?{param}={page}"
            try:
                links = parse_lov_listing(fetcher.get(url, [LOV_HOST]).body)
            except FetchError as error:
                audit.emit("lov_listing_error", url=url, error=str(error))
                break
            new = [link for link in links if link not in adverts]
            if not new:
                break
            adverts += new
            added_any = True
        if added_any:
            break
    unique = list(dict.fromkeys(adverts))
    audit.emit("lov_discovered", n=len(unique))
    return unique


# ----------------------------------------------------------- documents and text


def text_from_body(body: bytes, content_type: str = "") -> tuple[str, str]:
    """Return (kind, text). kind is 'pdf', 'html' or 'unsupported'."""
    if body.lstrip().startswith(b"%PDF-"):
        try:
            reader = PdfReader(io.BytesIO(body))
            if reader.is_encrypted and not reader.decrypt(""):
                return "pdf", ""
            pages = []
            for page in reader.pages:
                try:
                    pages.append(clean_text(page.extract_text() or ""))
                except Exception:  # noqa: BLE001 - a broken page should not stop the run
                    pages.append("")
            return "pdf", "\n\f\n".join(pages).strip()
        except Exception:  # noqa: BLE001
            return "pdf", ""
    lowered = body.lstrip()[:200].lower()
    if "html" in content_type.lower() or lowered.startswith((b"<!doctype html", b"<html")):
        soup = BeautifulSoup(body, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "noscript", "header"]):
            element.decompose()
        root = soup.find("main") or soup.find("article") or soup.body or soup
        return "html", clean_text(root.get_text("\n", strip=True))
    return "unsupported", ""


def pdf_links(html: bytes, base: str) -> list[str]:
    """PDF links on a municipality page, ranked so tender documents come first."""
    soup = BeautifulSoup(html, "html.parser")
    ranked: list[tuple[int, str]] = []
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base, anchor["href"])
        label = anchor.get_text(" ", strip=True) + " " + target
        is_pdf = ".pdf" in target.lower() or "/download/" in target.lower()
        if not is_pdf:
            continue
        score = 0 if _DOC_LINK_TEXT.search(label) else 1
        ranked.append((score, target))
    return list(dict.fromkeys(url for _, url in sorted(ranked, key=lambda item: item[0])))


@dataclass
class FetchedDocument:
    url: str
    kind: str
    sha256: str
    chars: int
    path: str
    error: str = ""


def fetch_documents(
    fetcher: Fetcher,
    urls: Iterable[str],
    out_dir: Path,
    audit: Audit,
    *,
    max_documents: int = 6,
    follow_html: bool = True,
) -> tuple[str, list[FetchedDocument]]:
    """Download documents (following one HTML hop to PDFs) and return combined text."""
    texts: list[str] = []
    records: list[FetchedDocument] = []
    queue = list(dict.fromkeys(urls))
    seen: set[str] = set()
    while queue and len([r for r in records if not r.error]) < max_documents:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        host = urlsplit(url).hostname or ""
        try:
            download = fetcher.get(url, [host])
        except FetchError as error:
            audit.emit("document_error", url=url, error=str(error))
            records.append(FetchedDocument(url, "error", "", 0, "", str(error)))
            continue
        kind, text = text_from_body(download.body, download.content_type)
        if kind == "html" and follow_html:
            queue = pdf_links(download.body, download.url) + queue
        folder = out_dir / download.sha256[:16]
        if kind == "pdf":
            atomic_bytes(folder / "source.pdf", download.body)
        if text:
            atomic_bytes(folder / "text.txt", text.encode("utf-8"))
            texts.append(text)
        records.append(
            FetchedDocument(
                download.url,
                kind,
                download.sha256,
                len(text),
                str(folder / "text.txt") if text else "",
                "" if text or kind == "html" else "no_extractable_text",
            )
        )
    return "\n\n".join(texts), records
