#!/usr/bin/env python3
"""One command: fetch -> download -> extract -> classify procurement documents.

Example:
    PYTHONPATH=src python scripts/run_procurement_pipeline.py \
        --contact din.epost@su.se --sources ted,lov \
        --from-year 2018 --to-year 2025 --output-dir research/procurement/pipeline

Re-running the same command reuses the HTTP and TED caches, so an interrupted run
resumes where it stopped. Use --offline to rebuild outputs from the cache only.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from municipal_research.config import Network
from municipal_research.network import Fetcher, FetchError
from municipal_research.procurement import aggregate_by_year, is_elderly_care, normalize_notice
from municipal_research.procurement_pipeline import (
    TedClient,
    discover_lov_adverts,
    document_urls_in_text,
    fetch_documents,
    parse_lov_advert,
    ted_default_query,
    ted_notice_to_row,
    text_from_body,
)
from municipal_research.storage import Audit, utc_now, write_json

LOV_HOST = "www.upphandlingsmyndigheten.se"


def run_ted(args, cache: Path, audit: Audit, fetcher: Fetcher, docs_dir: Path) -> list[dict]:
    client = TedClient(
        cache, audit, interval=args.interval, offline=args.offline, user_agent=args.user_agent
    )
    query = args.ted_query or ted_default_query(args.from_year, args.to_year)
    audit.emit("ted_query", query=query)
    rows, skipped = [], 0
    try:
        for number, notice in enumerate(client.search(query, max_pages=args.ted_max_pages), 1):
            row = ted_notice_to_row(notice)
            # Older TED notices return no description through the API, so fetch the
            # notice's own full Swedish text (public HTML on ted.europa.eu).
            notice_text = ""
            if row["notice_html_url"]:
                try:
                    page = fetcher.get(row["notice_html_url"], ["ted.europa.eu"])
                    _, notice_text = text_from_body(page.body, page.content_type)
                except FetchError as error:
                    audit.emit("ted_notice_error", url=row["notice_html_url"], error=str(error))
            screen = {"title": row["title"], "cpv": row["cpv"],
                      "description": row["description"] + "\n" + notice_text}
            if not args.all_services and not is_elderly_care(screen):
                skipped += 1
                audit.emit("ted_skipped_not_elderly_care", notice=row["notice_id"], title=row["title"])
                continue
            urls = list(dict.fromkeys(row["document_urls"] + document_urls_in_text(notice_text)))
            doc_text, records = "", []
            if args.ted_documents and urls:
                doc_text, records = fetch_documents(
                    fetcher, urls, docs_dir / "ted", audit, max_documents=args.max_documents,
                )
            row["notice_text_chars"] = len(notice_text)
            row["document_text"] = "\n\n".join(
                t for t in (row["description"], notice_text, doc_text) if t
            )
            row["documents"] = [asdict(r) for r in records]
            row["document_url"] = urls[0] if urls else ""
            rows.append(row)
            if number % 50 == 0:
                print(f"  TED: {number} annonser genomgångna, {len(rows)} äldreomsorg", flush=True)
    except FetchError as error:
        print(f"TED stoppade: {error}", file=sys.stderr)
        audit.emit("ted_error", error=str(error))
    print(f"  TED: {len(rows)} behållna, {skipped} bortfiltrerade (ej äldreomsorg)", flush=True)
    return rows


def run_lov(args, audit: Audit, fetcher: Fetcher, docs_dir: Path) -> list[dict]:
    seeds = []
    if args.lov_seed_file:
        seeds = [line.strip() for line in args.lov_seed_file.read_text(encoding="utf-8").splitlines()
                 if line.strip().startswith("http")]
    adverts = discover_lov_adverts(fetcher, audit, max_pages=args.lov_max_pages, seeds=seeds)
    print(f"  LOV: {len(adverts)} annonser hittade", flush=True)
    rows = []
    for number, url in enumerate(adverts, 1):
        try:
            page = fetcher.get(url, [LOV_HOST])
        except FetchError as error:
            audit.emit("lov_advert_error", url=url, error=str(error))
            continue
        advert = parse_lov_advert(page.body, page.url)
        screen = {"title": advert.title, "description": f"{advert.service_area} {advert.title}"}
        if not args.all_services and not is_elderly_care(screen):
            audit.emit("lov_skipped_not_elderly_care", url=url, title=advert.title)
            continue
        doc_text, records = fetch_documents(
            fetcher, advert.document_links, docs_dir / "lov", audit,
            max_documents=args.max_documents,
        )
        rows.append({
            "notice_id": advert.reference or url.rstrip("/").rsplit("/", 1)[-1],
            "publication_date": advert.updated or advert.start_date,
            "buyer_name": advert.buyer_name,
            "municipality_name": advert.buyer_name,
            "title": advert.title,
            "description": advert.service_area,
            "document_text": doc_text,
            "document_url": records[0].url if records else "",
            "documents": [asdict(r) for r in records],
            "source_url": url,
            # Adverts are the *currently published* LOV systems, dated by last update.
            "coverage_status": "lov_current_adverts",
        })
        if number % 25 == 0:
            print(f"  LOV: {number}/{len(adverts)} annonser behandlade", flush=True)
    return rows


def write_outputs(out: Path, source_rows: dict[str, list[dict]], args) -> None:
    notices = []
    for source, rows in source_rows.items():
        notices += [normalize_notice(row, source=source) for row in rows]
    with (out / "raw_rows.jsonl").open("w", encoding="utf-8") as handle:
        for source, rows in source_rows.items():
            for row in rows:
                slim = {k: v for k, v in row.items() if k != "document_text"}
                handle.write(json.dumps({"source": source, **slim}, ensure_ascii=False) + "\n")
    fields = list(notices[0].to_dict()) if notices else ["notice_id"]
    with (out / "notices.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(n.to_dict() for n in notices)
    summary = {
        source: aggregate_by_year([n for n in notices if n.source == source])
        for source in source_rows
    }
    write_json(out / "year_summary.json", summary)
    write_json(out / "manifest.json", {
        "finished_at": utc_now(),
        "arguments": {k: str(v) for k, v in vars(args).items()},
        "counts": {source: len(rows) for source, rows in source_rows.items()},
        "n_documents_downloaded": sum(
            1 for rows in source_rows.values() for row in rows
            for doc in row.get("documents", []) if doc.get("sha256")
        ),
        "notes": [
            "TED covers only notices above the EU thresholds.",
            "LOV rows are adverts published at run time; publication_date is the advert's "
            "last update, not the date the requirement was introduced.",
            "missing_document means no text could be retrieved, not that no requirement exists.",
        ],
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contact", required=True, help="E-mail put in the User-Agent so site owners can reach you.")
    parser.add_argument("--sources", default="ted,lov", help="Comma-separated: ted, lov")
    parser.add_argument("--output-dir", type=Path, default=Path("research/procurement/pipeline"))
    parser.add_argument("--from-year", type=int, default=2018)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    parser.add_argument("--ted-query", help="Override the TED expert query.")
    parser.add_argument("--ted-max-pages", type=int, default=50)
    parser.add_argument("--no-ted-documents", dest="ted_documents", action="store_false",
                        help="Classify TED notice text only; do not follow document links.")
    parser.add_argument("--lov-max-pages", type=int, default=60)
    parser.add_argument("--lov-seed-file", type=Path, help="Text file with extra advert URLs, one per line.")
    parser.add_argument("--all-services", action="store_true", help="Keep LOV adverts outside elderly care.")
    parser.add_argument("--max-documents", type=int, default=6, help="Per advert/notice.")
    parser.add_argument("--interval", type=float, default=1.5, help="Seconds between requests per host.")
    parser.add_argument("--offline", action="store_true", help="Use cache only.")
    args = parser.parse_args()
    args.user_agent = f"MunicipalResearch/0.1 (academic research; contact {args.contact})"

    out = args.output_dir
    cache, docs_dir = out / "cache", out / "documents"
    out.mkdir(parents=True, exist_ok=True)
    audit = Audit(out / "audit.jsonl")
    network = Network(user_agent=args.user_agent, interval_seconds=args.interval)
    fetcher = Fetcher(network, cache / "http", audit, offline=args.offline)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    results: dict[str, list[dict]] = {}
    try:
        if "ted" in sources:
            print("Hämtar från TED …", flush=True)
            results["ted"] = run_ted(args, cache, audit, fetcher, docs_dir)
        if "lov" in sources:
            print("Hämtar från Hitta LOV-uppdrag …", flush=True)
            results["lov"] = run_lov(args, audit, fetcher, docs_dir)
    finally:
        fetcher.close()
        write_outputs(out, results, args)
    for source, rows in results.items():
        explicit = sum(
            1 for r in rows if normalize_notice(r, source=source).language_category == "explicit_requirement"
        )
        print(f"{source}: {len(rows)} annonser, {explicit} med uttryckligt svenskkrav")
    print(f"Resultat i {out}/notices.csv, year_summary.json och manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
