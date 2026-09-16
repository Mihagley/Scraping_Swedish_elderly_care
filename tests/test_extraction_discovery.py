import io

import httpx
import pytest
from pypdf import PdfWriter

from municipal_research.config import Extraction
from municipal_research.demo import PILOT_QUOTE, pdf_fixture
from municipal_research.discovery import Discoverer
from municipal_research.extraction import chunk_document, extract
from municipal_research.network import Download, Fetcher
from municipal_research.storage import digest


def download(body, content_type="application/pdf"):
    return Download(
        requested_url="https://town.example/doc",
        url="https://town.example/doc",
        body=body,
        content_type=content_type,
        retrieved_at="2026-09-16T00:00:00Z",
        sha256=digest(body),
        status=200,
        cache_hit=False,
    )


def test_pdf_preserves_raw_and_page_offsets(tmp_path):
    raw = pdf_fixture()
    result = extract(download(raw), "demo", tmp_path, Extraction())
    assert (tmp_path / result.raw_path).read_bytes() == raw
    assert result.text_sha256 == digest((tmp_path / result.text_path).read_bytes())
    assert len(result.pages) == 2 and PILOT_QUOTE in result.text
    assert result.text[result.pages[1].start :].startswith("SYNTHETIC")


def test_blank_pdf_requires_review(tmp_path):
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)
    result = extract(download(buffer.getvalue()), "demo", tmp_path, Extraction())
    assert "scanned" in result.warnings[0]
    assert chunk_document(result, Extraction()) == ([], False)


def test_html_cleanup_and_offsets(tmp_path):
    body = "<html><head><meta charset='utf-8'><title>Å</title></head><body><main><p>En språkregel gäller äldreomsorgen.</p></main><script>evil()</script></body></html>".encode()
    result = extract(download(body, "text/html"), "demo", tmp_path, Extraction())
    assert "evil()" not in result.text
    assert "språkregel" in result.text
    assert result.title == "Å"


@pytest.mark.parametrize("title", ["Sidan saknas | MeetingPlus [sv]", "404 - Page not found"])
def test_soft_404_preserved_but_not_classified(tmp_path, title):
    body = f"<html><head><title>{title}</title></head><body><main>Requested document missing.</main></body></html>".encode()
    result = extract(download(body, "text/html"), "demo", tmp_path, Extraction())
    assert (tmp_path / result.raw_path).read_bytes() == body
    assert (tmp_path / result.text_path).is_file()
    assert "suspected_error_page_not_classified" in result.warnings
    assert chunk_document(result, Extraction()) == ([], False)


def test_chunks_cover_text_and_report_limit(document):
    document.text *= 10
    config = Extraction(chunk_chars=100, overlap_chars=20, max_chunks_per_document=100)
    chunks, limited = chunk_document(document, config)
    assert not limited
    assert chunks[0].start == 0 and chunks[-1].end == len(document.text)
    for first, second in zip(chunks, chunks[1:], strict=False):
        assert first.start < second.start <= first.end
        assert first.text == document.text[first.start : first.end]
    config.max_chunks_per_document = 1
    assert chunk_document(document, config)[1]


def test_sitemap_and_html_link_discovery(config, municipality, tmp_path, audit):
    config.discovery.provider = "crawl"
    config.discovery.keywords = ["policy"]
    config.discovery.max_sitemaps = 3
    config.discovery.max_documents = 10

    def respond(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\nSitemap: https://town.example/sitemap.xml"
            )
        if path == "/sitemap.xml":
            return httpx.Response(
                200,
                text="<sitemapindex><sitemap><loc>https://town.example/child.xml</loc></sitemap></sitemapindex>",
            )
        if path == "/child.xml":
            return httpx.Response(
                200, text="<urlset><url><loc>https://town.example/policy</loc></url></urlset>"
            )
        return httpx.Response(
            200,
            text='<html><a href="/linked-policy">Policy</a><a href="https://outside.example/a">Outside</a></html>',
            headers={"content-type": "text/html"},
        )

    fetcher = Fetcher(
        config.network,
        tmp_path / "cache",
        audit,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        address_check=lambda url: None,
    )
    discovery = Discoverer(config, fetcher, None, audit)
    urls = [d.url for d in discovery.documents(municipality)]
    assert "https://town.example/policy" in urls and "https://town.example/linked-policy" in urls
    assert all("outside" not in url for url in urls)
    assert any(e["method"] == "sitemap" for e in discovery.events)
