from __future__ import annotations

import gzip
import heapq
import io
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .config import Config, Municipality
from .llm import Gateway
from .network import Fetcher, domain_allowed, normalize_url
from .storage import Audit


class Discoverer:
    def __init__(self, config: Config, fetcher: Fetcher, gateway: Gateway, audit: Audit):
        self.config, self.fetcher, self.gateway, self.audit = config, fetcher, gateway, audit
        self.events: list[dict] = []
        self.gaps: list[str] = []
        self.queue: list[tuple] = []
        self.seen: set[str] = set()
        self.sequence = 0

    def score(self, value: str) -> int:
        value = unquote(value).casefold()
        return sum(1 for word in self.config.discovery.keywords if word.casefold() in value)

    def event(self, **data):
        self.events.append(data)
        self.audit.emit("discovery", **data)

    def add(
        self,
        municipality: Municipality,
        url: str,
        method: str,
        parent: str = "",
        depth: int = 0,
        title: str = "",
        priority: int = 0,
    ):
        try:
            url = normalize_url(url)
        except (ValueError, RuntimeError):
            return
        if url in self.seen or not domain_allowed(url, municipality.allowed_domains):
            return
        self.seen.add(url)
        score = self.score(url + " " + title) + priority
        self.sequence += 1
        heapq.heappush(self.queue, (-score, depth, self.sequence, url))
        self.event(
            municipality_id=municipality.id,
            url=url,
            method=method,
            parent=parent,
            depth=depth,
            title=title,
            score=score,
            outcome="candidate",
        )

    def sitemaps(self, municipality: Municipality):
        pending = [f"https://{d}/sitemap.xml" for d in municipality.domains]
        for domain in municipality.domains:
            try:
                robots = self.fetcher.robot_policy(f"https://{domain}/", municipality.domains)
                pending.extend(robots.site_maps() or [])
            except Exception as error:
                self.event(
                    municipality_id=municipality.id,
                    url=domain,
                    method="robots_sitemaps",
                    outcome="error",
                    error=str(error),
                )
        visited, url_count = set(), 0
        while pending and len(visited) < self.config.discovery.max_sitemaps:
            url = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                download = self.fetcher.get(url, municipality.domains)
                body = download.body
                if body.startswith(b"\x1f\x8b"):
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                        body = compressed.read(self.config.network.max_bytes + 1)
                if len(body) > self.config.network.max_bytes:
                    raise ValueError("Decompressed sitemap too large")
                if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                    raise ValueError("XML entity declarations are not supported")
                root = ElementTree.fromstring(body)
                is_index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
                for node in root.iter():
                    if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
                        continue
                    location = node.text.strip()
                    if is_index:
                        pending.append(location)
                    else:
                        url_count += 1
                        if url_count > self.config.discovery.max_sitemap_urls:
                            self.gaps.append("sitemap_url_limit")
                            return
                        if self.score(location) or not self.config.discovery.keywords:
                            self.add(municipality, location, "sitemap", parent=url)
            except Exception as error:
                self.event(
                    municipality_id=municipality.id,
                    url=url,
                    method="sitemap",
                    outcome="error",
                    error=str(error),
                )
        if pending:
            self.gaps.append("sitemap_limit")

    def documents(self, municipality: Municipality):
        settings = self.config.discovery
        for archive in municipality.meeting_archives:
            self.add(municipality, archive, "meeting_archive", priority=1500)
        for seed in municipality.seeds:
            self.add(municipality, seed, "seed", priority=1000)
        for domain in municipality.domains:
            self.add(municipality, "https://" + domain + "/", "homepage")
        if settings.provider in {"openai", "hybrid"}:
            queries = settings.queries or ["{municipality}: {question}"]
            for template in queries[: settings.max_searches_per_municipality]:
                query = template.format(
                    municipality=municipality.name,
                    domain=municipality.domains[0],
                    question=self.config.research.question,
                )
                try:
                    for source in self.gateway.search(query, municipality.domains):
                        self.add(
                            municipality,
                            source["url"],
                            "web_search",
                            parent=query,
                            title=source["title"],
                            priority=100,
                        )
                except Exception as error:
                    self.gaps.append("search_failed")
                    self.event(
                        municipality_id=municipality.id,
                        url="",
                        method="web_search",
                        parent=query,
                        outcome="error",
                        error=str(error),
                    )
        if settings.provider in {"crawl", "hybrid"} and settings.max_sitemaps:
            self.sitemaps(municipality)
        attempted, delivered = 0, set()
        while self.queue and attempted < settings.max_documents:
            _, depth, _, url = heapq.heappop(self.queue)
            attempted += 1
            try:
                download = self.fetcher.get(url, municipality.allowed_domains)
                identity = (download.url, download.sha256)
                if identity in delivered:
                    continue
                delivered.add(identity)
                self.event(
                    municipality_id=municipality.id,
                    url=url,
                    final_url=download.url,
                    method="download",
                    outcome="downloaded",
                )
                if "html" in download.content_type.lower() and settings.provider in {
                    "crawl",
                    "hybrid",
                }:
                    if depth < settings.max_depth:
                        soup = BeautifulSoup(download.body, "html.parser")
                        for anchor in soup.select("a[href]"):
                            self.add(
                                municipality,
                                urljoin(download.url, anchor["href"]),
                                "link",
                                parent=download.url,
                                depth=depth + 1,
                                title=anchor.get_text(" ", strip=True),
                            )
                    else:
                        self.gaps.append("crawl_depth_limit")
                yield download
            except Exception as error:
                self.gaps.append("download_failed")
                self.event(
                    municipality_id=municipality.id,
                    url=url,
                    method="download",
                    outcome="error",
                    error=str(error),
                )
        if self.queue:
            self.gaps.append("document_limit")
