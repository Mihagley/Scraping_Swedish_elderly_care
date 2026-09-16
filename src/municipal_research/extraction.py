from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path

import trafilatura
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .config import Extraction
from .models import Chunk, Document, Page
from .network import Download
from .storage import atomic_bytes, digest, write_json

EXTRACTOR_VERSION = "text-v1"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "").replace("\u00ad", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract(download: Download, municipality_id: str, run: Path, config: Extraction) -> Document:
    is_pdf = download.body.lstrip().startswith(b"%PDF-")
    media = "pdf" if is_pdf else "html"
    identifier = digest(municipality_id + "\n" + download.url + "\n" + download.sha256)[:24]
    folder = run / "sources" / identifier
    raw = folder / ("source.pdf" if is_pdf else "source.html")
    # Preserve the received body even when decoding or extraction subsequently fails.
    atomic_bytes(raw, download.body)
    write_json(
        folder / "download.json",
        {
            "url": download.url,
            "requested_url": download.requested_url,
            "retrieved_at": download.retrieved_at,
            "sha256": download.sha256,
            "content_type": download.content_type,
            "raw_path": str(raw.relative_to(run)),
        },
    )
    warnings, pages = [], []
    if is_pdf:
        reader = PdfReader(io.BytesIO(download.body))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("Encrypted PDF cannot be extracted")
        title = str((reader.metadata or {}).get("/Title", "") or "")
        pieces, offset = [], 0
        for number, page in enumerate(reader.pages, 1):
            try:
                text = clean_text(page.extract_text() or "")
            except Exception as error:
                text = ""
                warnings.append(f"page_{number}_extraction_failed:{type(error).__name__}")
            if len(text) < config.min_page_chars:
                warnings.append(f"page_{number}_sparse_or_scanned_needs_review")
            pages.append(Page(page=number, start=offset, end=offset + len(text)))
            pieces.append(text)
            offset += len(text) + 3
        text = "\n\f\n".join(pieces)
        extractor = "pypdf/" + EXTRACTOR_VERSION
    else:
        if not (
            "html" in download.content_type.lower()
            or download.body.lstrip().lower().startswith((b"<!doctype html", b"<html"))
        ):
            raise ValueError("Unsupported content type: " + download.content_type)
        soup = BeautifulSoup(download.body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        value = trafilatura.extract(
            download.body,
            include_tables=True,
            include_comments=False,
            output_format="txt",
            favor_recall=True,
        )
        extractor = "trafilatura/" + EXTRACTOR_VERSION
        if not value:
            for element in soup(["script", "style", "nav", "footer", "noscript", "header"]):
                element.decompose()
            root = soup.find("main") or soup.find("article") or soup.body or soup
            value = root.get_text("\n", strip=True)
            warnings.append("html_fallback_extractor_review_layout")
            extractor = "beautifulsoup/" + EXTRACTOR_VERSION
        text = clean_text(value)
        if len(text) < config.min_page_chars:
            warnings.append("sparse_html_possible_javascript_or_empty_page")
        pages = [Page(page=None, start=0, end=len(text))]
    text_path, numbered = folder / "text.txt", folder / "text.lines.txt"
    atomic_bytes(text_path, text.encode("utf-8"))
    atomic_bytes(
        numbered,
        "\n".join(f"{i:06d}\t{line}" for i, line in enumerate(text.split("\n"), 1)).encode("utf-8"),
    )
    document = Document(
        id=identifier,
        municipality_id=municipality_id,
        url=download.url,
        requested_url=download.requested_url,
        retrieved_at=download.retrieved_at,
        raw_sha256=download.sha256,
        text_sha256=digest(text),
        raw_path=raw.relative_to(run).as_posix(),
        text_path=text_path.relative_to(run).as_posix(),
        numbered_path=numbered.relative_to(run).as_posix(),
        media_type=media,
        title=title,
        text=text,
        pages=pages,
        warnings=warnings,
        extractor=extractor,
    )
    write_json(folder / "document.json", document.model_dump(mode="json", exclude={"text"}))
    return document


def chunk_document(document: Document, config: Extraction) -> tuple[list[Chunk], bool]:
    chunks, start = [], 0
    while start < len(document.text) and len(chunks) < config.max_chunks_per_document:
        end = min(start + config.chunk_chars, len(document.text))
        if end < len(document.text):
            boundary = document.text.rfind("\n", start + config.chunk_chars // 2, end)
            if boundary > start + config.overlap_chars:
                end = boundary + 1
        chunks.append(
            Chunk(
                id=f"{document.id}:{start}:{end}",
                document_id=document.id,
                start=start,
                end=end,
                text=document.text[start:end],
            )
        )
        if end == len(document.text):
            return chunks, False
        start = max(start + 1, end - config.overlap_chars)
    return chunks, bool(chunks and chunks[-1].end < len(document.text))
