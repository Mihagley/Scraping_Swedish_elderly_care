import io
import json

import httpx
from reportlab.pdfgen import canvas

from municipal_research.config import Network
from municipal_research.network import Fetcher
from municipal_research.procurement import normalize_notice
from municipal_research.procurement_pipeline import (
    TedClient,
    fetch_documents,
    parse_lov_advert,
    parse_lov_listing,
    ted_default_query,
    ted_notice_to_row,
)
from municipal_research.storage import Audit


def _pdf(text: str) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, text)
    page.save()
    return buffer.getvalue()


LISTING = """<html><body><main>
<a href="/hitta-lov-uppdrag/annonser/skovde-kommun/valfrihetssystem-inom-hemtjanst/">Skövde</a>
<a href="/hitta-lov-uppdrag/annonser/region-x/medicinsk-fotvard/">Fotvård</a>
<a href="/hitta-lov-uppdrag/tips-och-hjalp/">Tips</a>
<a href="https://example.org/other">Extern</a>
</main></body></html>"""

ADVERT = """<html><body><main>
<h1>Skövde kommun - Valfrihetssystem inom hemtjänst</h1>
<dl><dt>Tjänsteområde</dt><dd>Hemtjänst och service</dd>
<dt>Diarienummer</dt><dd>VON2024.0212</dd>
<dt>Senast uppdaterad</dt><dd>2025-04-09</dd></dl>
<a href="https://www.skovde.se/leverantor-hemtjanst/">Förfrågningsunderlag</a>
<a href="https://www.upphandlingsmyndigheten.se/frageportalen/">Frågeportalen</a>
</main></body></html>"""

MUNICIPALITY_PAGE = """<html><body><main><h1>Leverantör hemtjänst</h1>
<a href="/download/forfragningsunderlag-hemtjanst.pdf">Förfrågningsunderlag LOV hemtjänst</a>
<a href="/download/karta.pdf">Karta</a>
</main></body></html>"""


def _fetcher(tmp_path, handler):
    return Fetcher(
        Network(interval_seconds=0),
        tmp_path / "cache",
        Audit(tmp_path / "audit.jsonl"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        address_check=lambda url: None,
        sleep=lambda seconds: None,
    )


def test_listing_keeps_only_advert_urls():
    assert parse_lov_listing(LISTING) == [
        "https://www.upphandlingsmyndigheten.se/hitta-lov-uppdrag/annonser/skovde-kommun/valfrihetssystem-inom-hemtjanst/",
        "https://www.upphandlingsmyndigheten.se/hitta-lov-uppdrag/annonser/region-x/medicinsk-fotvard/",
    ]


def test_advert_parsing_finds_metadata_and_external_document_link():
    url = "https://www.upphandlingsmyndigheten.se/hitta-lov-uppdrag/annonser/skovde-kommun/valfrihetssystem-inom-hemtjanst/"
    advert = parse_lov_advert(ADVERT, url)
    assert advert.title.startswith("Skövde kommun")
    assert advert.service_area == "Hemtjänst och service"
    assert advert.reference == "VON2024.0212"
    assert advert.updated == "2025-04-09"
    assert advert.document_links == ["https://www.skovde.se/leverantor-hemtjanst/"]


def test_documents_follow_one_html_hop_to_tender_pdf(tmp_path):
    pdf = _pdf("Personalen ska kunna tala, lasa och skriva svenska.")

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/leverantor-hemtjanst/":
            return httpx.Response(200, content=MUNICIPALITY_PAGE, headers={"content-type": "text/html"})
        if path == "/download/forfragningsunderlag-hemtjanst.pdf":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    fetcher = _fetcher(tmp_path, handler)
    text, records = fetch_documents(
        fetcher, ["https://www.skovde.se/leverantor-hemtjanst/"], tmp_path / "docs",
        Audit(tmp_path / "audit.jsonl"), max_documents=2,
    )
    kinds = [r.kind for r in records]
    assert kinds[:2] == ["html", "pdf"]  # tender PDF ranked before the map
    assert "tala" in text
    assert records[1].sha256 and (tmp_path / "docs").exists()


def test_unreachable_document_is_recorded_not_dropped(tmp_path):
    fetcher = _fetcher(tmp_path, lambda request: httpx.Response(404))
    text, records = fetch_documents(
        fetcher, ["https://www.example.se/x.pdf"], tmp_path / "docs", Audit(tmp_path / "a.jsonl")
    )
    assert text == "" and records[0].kind == "error"


def test_ted_iteration_cache_and_row_mapping(tmp_path):
    calls = []
    pages = [
        {"notices": [{
            "publication-number": "123456-2021",
            "publication-date": "2021-03-04+01:00",
            "notice-title": {"swe": ["Drift av vård- och omsorgsboende"]},
            "buyer-name": {"swe": ["Testkommun"]},
            "buyer-city": {"swe": ["Teststad"]},
            "classification-cpv": ["85311100"],
            "description-lot": {"swe": ["Personalen ska behärska svenska i tal och skrift."]},
            "document-url-lot": ["https://www.e-avrop.com/x/docs"],
        }], "iterationNextToken": "t1"},
        {"notices": [], "iterationNextToken": None},
    ]

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=pages[1] if body.get("iterationNextToken") else pages[0])

    audit = Audit(tmp_path / "audit.jsonl")
    client = TedClient(tmp_path, audit, client=httpx.Client(transport=httpx.MockTransport(handler)), interval=0)
    notices = list(client.search(ted_default_query(2018, 2025)))
    assert len(notices) == 1 and calls[1]["iterationNextToken"] == "t1"
    assert "buyer-country IN (SWE)" in calls[0]["query"] and "85311100" in calls[0]["query"]

    # Second run is served entirely from the cache.
    offline = TedClient(tmp_path, audit, client=httpx.Client(transport=httpx.MockTransport(lambda r: 1 / 0)), offline=True)
    assert len(list(offline.search(ted_default_query(2018, 2025)))) == 1

    row = ted_notice_to_row(notices[0])
    assert row["publication_date"] == "2021-03-04"
    assert row["municipality_name"] == "Teststad"
    assert row["document_urls"] == ["https://www.e-avrop.com/x/docs"]
    row["document_text"] = row["description"]
    notice = normalize_notice(row, source="ted")
    assert notice.year == 2021 and notice.language_category == "explicit_requirement"


def test_ted_network_failure_raises_fetch_error_not_crash(tmp_path):
    import pytest

    from municipal_research.network import FetchError

    def handler(request):
        raise httpx.ConnectError("blocked")

    client = TedClient(tmp_path, Audit(tmp_path / "a.jsonl"), client=httpx.Client(transport=httpx.MockTransport(handler)), interval=0)
    client.pacer.sleep = lambda seconds: None
    with pytest.raises(FetchError):
        list(client.search("x"))


def test_ted_row_uses_swedish_direct_html_link_and_finds_document_links():
    from municipal_research.procurement_pipeline import document_urls_in_text

    notice = {
        "publication-number": "12196-2018",
        "notice-title": {"eng": "Sweden-Örebro: Social work", "swe": "Sverige-Örebro: Social omsorg med inkvartering"},
        "links": {"htmlDirect": {"ENG": "https://ted.europa.eu/en/notice/12196-2018/html",
                                  "SWE": "https://ted.europa.eu/sv/notice/12196-2018/html"}},
    }
    row = ted_notice_to_row(notice)
    assert row["title"].startswith("Sverige-Örebro")
    assert row["notice_html_url"] == "https://ted.europa.eu/sv/notice/12196-2018/html"
    text = ("Upphandlingsdokumenten finns på https://www.e-avrop.com/orebro/e-Upphandling/Default.aspx. "
            "Se även www.tendsign.com och https://ted.europa.eu/x samt EUR-Lex.")
    assert document_urls_in_text(text) == [
        "https://www.e-avrop.com/orebro/e-Upphandling/Default.aspx",
        "https://www.tendsign.com",
    ]


def test_dead_domain_is_recorded_not_raised(tmp_path):
    import socket

    from municipal_research.network import require_public_address

    fetcher = Fetcher(
        Network(interval_seconds=0), tmp_path / "cache", Audit(tmp_path / "a.jsonl"),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        address_check=require_public_address, sleep=lambda s: None,
    )
    original = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(11001, "getaddrinfo failed"))
    try:
        text, records = fetch_documents(
            fetcher, ["https://no-such-domain.example/doc.pdf"], tmp_path / "docs", Audit(tmp_path / "a.jsonl")
        )
    finally:
        socket.getaddrinfo = original
    assert text == "" and records[0].kind == "error" and "DNS" in records[0].error
