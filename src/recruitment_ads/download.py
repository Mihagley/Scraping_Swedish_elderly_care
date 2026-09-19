"""Immutable, checksum-verified local caches of official annual JSONL files."""

import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

BASE = "https://data.jobtechdev.se/annonser/historiska/"


def sha256(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def annual_url(year, sample=False):
    if not 2016 <= year <= 2025:
        raise ValueError(
            "Annual adapter supports 2016–2025. Use explicit quarterly sources for 2026."
        )
    kind = "exempel" if sample else "kompletta"
    suffix = "_1_percent" if sample else ""
    return f"{BASE}berikade/{kind}/{year}_beta1{suffix}_jsonl.zip"


def download(url, cache, *, year=None, data_version="beta1", sample=False):
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or host != "data.jobtechdev.se":
        raise ValueError("Advertisement downloads must use the official HTTPS JobTech archive")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / Path(urlparse(url).path).name
    manifest = path.with_suffix(path.suffix + ".manifest.json")
    if path.exists() or manifest.exists():
        if not path.exists() or not manifest.exists():
            raise ValueError(f"Incomplete cache provenance: {path}; use a new cache directory")
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        if meta["source_url"] != url or meta["source_hash"] != sha256(path):
            raise ValueError(f"Cache/source mismatch: {path}; refusing replacement")
        if (
            meta["source_year"] != year
            or meta["is_sample"] != sample
            or meta["data_version"] != data_version
        ):
            raise ValueError("Cached source metadata differs from requested source")
        return path, meta
    part = path.with_suffix(path.suffix + ".part")
    # Failed partial downloads are retried from byte zero. Completed files are never replaced.
    for attempt in range(3):
        try:
            with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
                response.raise_for_status()
                with part.open("wb") as f:
                    for chunk in response.iter_bytes(1024 * 1024):
                        f.write(chunk)
                headers = dict(response.headers)
            if (
                headers.get("content-length")
                and int(headers["content-length"]) != part.stat().st_size
            ):
                raise ValueError("Truncated download")
            with zipfile.ZipFile(part) as archive:
                if not any(n.endswith(".jsonl") for n in archive.namelist()):
                    raise ValueError("Archive has no JSONL member")
            break
        except (httpx.HTTPError, OSError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    meta = {
        "source_url": url,
        "source_file": path.name,
        "source_hash": sha256(part),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "data_version": data_version,
        "source_year": year,
        "is_sample": sample,
        "bytes": part.stat().st_size,
        "etag": headers.get("etag"),
        "last_modified": headers.get("last-modified"),
        "calendar_complete": bool(year and 2016 <= year <= 2025 and not sample),
    }
    part.replace(path)
    write_json(manifest, meta)
    return path, meta


def iter_ads(path):
    """Stream without extracting archives or loading a year into memory; fail on bad records."""
    path = Path(path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = sorted(n for n in archive.namelist() if n.endswith(".jsonl"))
            if not names:
                raise ValueError("No JSONL in archive")
            for name in names:
                with archive.open(name) as f:
                    yield from _lines(f, name)
    else:
        with path.open("rb") as f:
            yield from _lines(f, path.name)


def _lines(f, name):
    for line_no, line in enumerate(f, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("Expected one JSON object per line")
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid record {name}:{line_no}") from exc
        yield value, name, line_no
