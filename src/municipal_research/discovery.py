from __future__ import annotations

import gzip
import heapq
import io
import json
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .budget import BudgetExhausted
from .config import Config, Municipality
from .llm import Gateway
from .network import Fetcher, domain_allowed, normalize_url
from .storage import Audit, write_json


class Discoverer:
    def __init__(self, config: Config, fetcher: Fetcher, gateway: Gateway, audit: Audit, *, checkpoint=None):
        self.config, self.fetcher, self.gateway, self.audit = config, fetcher, gateway, audit
        self.events: list[dict] = []
        self.gaps: list[str] = []
        self.queue: list[tuple] = []
        self.seen: set[str] = set()
        self.sequence = 0
        self.budget = getattr(fetcher, "budget", None)
        self.attempted = 0
        self.inflight = None
        self.restored = checkpoint is not None
        if checkpoint:
            self.queue = [tuple(row) for row in checkpoint["queue"]]
            self.seen = set(checkpoint["seen"])
            self.sequence = checkpoint["sequence"]
            if checkpoint.get("inflight"):
                heapq.heappush(self.queue, tuple(checkpoint["inflight"]))

    def matching_terms(self, value):
        value = unquote(value).casefold()
        return [word for word in self.config.discovery.keywords if word.casefold() in value]

    def score(self, value: str) -> int:
        return len(self.matching_terms(value))

    def checkpoint(self, municipality):
        write_json(self.audit.path.parent / "checkpoints" / (municipality.id + ".json"), {"queue": self.queue, "seen": sorted(self.seen), "sequence": self.sequence, "inflight": self.inflight, "attempts_this_run": self.attempted})

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
        if len(self.seen) >= self.config.discovery.max_sitemap_urls:
            if "candidate_queue_limit" not in self.gaps:
                self.gaps.append("candidate_queue_limit")
            return
        if not domain_allowed(url, municipality.allowed_domains):
            return
        score = self.score(url + " " + title) + priority
        if url in self.seen:
            for index, item in enumerate(self.queue):
                if item[3] == url and -score < item[0]:
                    self.queue[index] = (-score, item[1], item[2], url)
                    heapq.heapify(self.queue)
                    self.event(municipality_id=municipality.id, url=url, method=method, parent=parent, title=title, matched_terms=self.matching_terms(title + " " + url), outcome="rank_updated")
                    break
            return
        self.seen.add(url)
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
            matched_terms=self.matching_terms(url + " " + title),
            outcome="candidate",
        )

    def sitemaps(self, municipality: Municipality):
        pending = [f"https://{d}/sitemap.xml" for d in municipality.allowed_domains]
        for domain in municipality.domains:
            try:
                robots = self.fetcher.robot_policy(f"https://{domain}/", municipality.allowed_domains)
                pending.extend(robots.site_maps() or [])
            except BudgetExhausted:
                raise
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
                download = self.fetcher.get(url, municipality.allowed_domains, kind="sitemap")
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
                        # Include opaque URLs too; terms rank rather than filter them out.
                        self.add(municipality, location, "sitemap", parent=url, priority=int(location.lower().split("?")[0].endswith(".pdf")))
            except BudgetExhausted:
                raise
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
        if not self.restored:
            for seed in municipality.seeds + municipality.public_endpoints:
                self.add(municipality, seed, "seed", priority=1000)
            for domain in municipality.domains:
                self.add(municipality, "https://" + domain + "/", "homepage")
            self.checkpoint(municipality)
            if settings.provider in {"openai", "hybrid"}:
                queries = settings.queries or ["{municipality}: {question}"]
                for template in queries[: settings.max_searches_per_municipality]:
                    query = template.format(
                        municipality=municipality.name,
                        location=municipality.name,
                        region=municipality.name if municipality.kind == "region" else "",
                        domain=municipality.allowed_domains[0],
                        question=self.config.research.question,
                    )
                    try:
                        for source in self.gateway.search(query, municipality.allowed_domains):
                            self.add(
                                municipality,
                                source["url"],
                                "web_search",
                                parent=query,
                                title=source["title"],
                                priority=100,
                            )
                    except BudgetExhausted:
                        raise
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
        delivered = set()
        self.checkpoint(municipality)
        while self.queue and self.attempted < settings.max_documents:
            if self.budget:
                self.budget.check()
            item = heapq.heappop(self.queue)
            _, depth, _, url = item
            self.inflight = item
            self.attempted += 1
            if self.budget:
                self.budget.count("attempted_retrievals")
            self.checkpoint(municipality)
            try:
                download = self.fetcher.get(url, municipality.allowed_domains)
                identity = (download.url, download.sha256)
                if identity in delivered:
                    self.inflight = None
                    self.checkpoint(municipality)
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
                if download.requested_url in municipality.public_endpoints and "json" in download.content_type.lower():
                    payload = json.loads(download.body)
                    pending_values, visited_values = [payload], 0
                    while pending_values and visited_values < settings.max_sitemap_urls:
                        value = pending_values.pop()
                        visited_values += 1
                        if isinstance(value, dict):
                            pending_values.extend(list(value.values())[:settings.max_sitemap_urls])
                        elif isinstance(value, list):
                            pending_values.extend(value[:settings.max_sitemap_urls])
                        elif isinstance(value, str) and value.startswith(("https://", "http://")):
                            self.add(municipality, value, "configured_public_endpoint", parent=download.url, depth=depth + 1)
                yield download
                self.inflight = None
                self.checkpoint(municipality)
            except BudgetExhausted:
                raise
            except Exception as error:
                self.inflight = None
                if self.budget:
                    self.budget.count("failed_downloads")
                self.checkpoint(municipality)
                self.gaps.append("download_failed")
                self.event(
                    municipality_id=municipality.id,
                    url=url,
                    method="download",
                    outcome="error",
                    error=str(error),
                )
        if self.queue:
            self.gaps.append("candidate_attempt_limit")
        self.checkpoint(municipality)
