from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import Network
from .storage import Audit, atomic_bytes, digest, read_json, utc_now, write_json


class FetchError(RuntimeError):
    pass


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchError("Only absolute HTTP(S) URLs are supported")
    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.username or parsed.password or parsed.port not in {None, default_port}:
        raise FetchError("Credentials and nonstandard ports are not allowed")
    host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    # Keep query order and escaping: changing them can invalidate document URLs.
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", parsed.query, ""))


def domain_allowed(url: str, domains: list[str]) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in domains)


def require_public_address(url: str) -> None:
    host = urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise FetchError(f"DNS lookup failed for {host}: {error}") from error
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise FetchError("Non-public network address rejected")


class Pacer:
    """Single-process, per-key request spacing; no threads are used by this pipeline."""

    def __init__(self, interval: float, *, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.last: dict[str, float] = {}

    def wait(self, key: str, minimum: float = 0) -> None:
        remaining = self.last.get(key, -float("inf")) + max(self.interval, minimum) - self.clock()
        if remaining > 0:
            self.sleep(remaining)
        self.last[key] = self.clock()


def retry_delay(value: str | None, attempt: int) -> float:
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(
                    0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                )
            except (ValueError, TypeError):
                pass
    return min(2**attempt, 30)


@dataclass
class Download:
    requested_url: str
    url: str
    body: bytes
    content_type: str
    retrieved_at: str
    sha256: str
    status: int
    cache_hit: bool


class Fetcher:
    def __init__(
        self,
        config: Network,
        cache: Path,
        audit: Audit,
        *,
        offline=False,
        refresh=False,
        client: httpx.Client | None = None,
        address_check=require_public_address,
        sleep=time.sleep,
    ):
        self.config, self.cache, self.audit = config, cache, audit
        self.offline, self.refresh = offline, refresh
        self.client = client or httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": config.user_agent},
        )
        self.address_check, self.sleep = address_check, sleep
        self.pacer = Pacer(config.interval_seconds, sleep=sleep)
        self.robots: dict[str, RobotFileParser] = {}

    def close(self):
        self.client.close()

    def _cached(self, url: str, domains: list[str]) -> Download | None:
        index = self.cache / "urls" / (digest(url) + ".json")
        if not index.exists() or (self.refresh and not self.offline):
            return None
        meta = read_json(index)
        if not domain_allowed(meta["url"], domains):
            raise FetchError("Cached redirect target is outside allowed domains")
        body = (self.cache / "bodies" / meta["sha256"]).read_bytes()
        if digest(body) != meta["sha256"]:
            raise FetchError("HTTP cache integrity check failed")
        return Download(**meta, body=body, cache_hit=True)

    def _request(self, url: str, domains: list[str], *, robots=False) -> Download:
        original = url = normalize_url(url)
        if not domain_allowed(url, domains):
            raise FetchError("URL is outside municipality domain allowlist")
        cached = self._cached(url, domains)
        if cached:
            self.audit.emit(
                "fetch",
                url=url,
                final_url=cached.url,
                cache_hit=True,
                sha256=cached.sha256,
                status=cached.status,
            )
            return cached
        if self.offline:
            raise FetchError("Offline HTTP cache miss: " + url)
        for redirect in range(self.config.max_redirects + 1):
            if not domain_allowed(url, domains):
                raise FetchError("Redirect left municipality domain allowlist")
            self.address_check(url)
            crawl_delay = 0.0
            if self.config.respect_robots and not robots:
                policy = self.robot_policy(url, domains)
                if not policy.can_fetch(self.config.user_agent, url):
                    raise FetchError("robots.txt disallows URL")
                crawl_delay = float(policy.crawl_delay(self.config.user_agent) or 0)
                rate = policy.request_rate(self.config.user_agent)
                if rate and rate.requests:
                    crawl_delay = max(crawl_delay, rate.seconds / rate.requests)
            for attempt in range(self.config.retries + 1):
                self.pacer.wait(urlsplit(url).netloc, crawl_delay)
                try:
                    with self.client.stream("GET", url) as response:
                        status, headers = response.status_code, response.headers
                        if status == 429 or status in {500, 502, 503, 504}:
                            if attempt == self.config.retries:
                                raise FetchError(f"HTTP {status}: retries exhausted")
                            delay = retry_delay(headers.get("retry-after"), attempt)
                            self.audit.emit("http_retry", url=url, status=status, delay=delay)
                            self.sleep(delay)
                            continue
                        parts, size = [], 0
                        for part in response.iter_bytes():
                            size += len(part)
                            if size > self.config.max_bytes:
                                raise FetchError("Response exceeds max_bytes")
                            parts.append(part)
                        body = b"".join(parts)
                    break
                except httpx.TransportError as error:
                    if attempt == self.config.retries:
                        raise FetchError(f"Transport error: {type(error).__name__}") from error
                    self.audit.emit("http_retry", url=url, error=type(error).__name__)
                    self.sleep(retry_delay(None, attempt))
            if status in {301, 302, 303, 307, 308}:
                if redirect == self.config.max_redirects or not headers.get("location"):
                    raise FetchError("Invalid or excessive redirects")
                target = normalize_url(urljoin(url, headers["location"]))
                self.audit.emit("redirect", url=url, target=target)
                url = target
                continue
            if status >= 400 and not (robots and status in {401, 403, 404, 410}):
                raise FetchError(f"HTTP {status}")
            sha = digest(body)
            meta = dict(
                requested_url=original,
                url=url,
                content_type=headers.get("content-type", ""),
                retrieved_at=utc_now(),
                sha256=sha,
                status=status,
            )
            atomic_bytes(self.cache / "bodies" / sha, body)
            write_json(self.cache / "urls" / (digest(original) + ".json"), meta)
            self.audit.emit(
                "fetch", url=original, final_url=url, status=status, sha256=sha, cache_hit=False
            )
            return Download(**meta, body=body, cache_hit=False)
        raise FetchError("Redirect limit reached")

    def robot_policy(self, url: str, domains: list[str]) -> RobotFileParser:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            result = self._request(origin + "/robots.txt", domains, robots=True)
            parser = RobotFileParser()
            if result.status in {401, 403}:
                parser.parse(["User-agent: *", "Disallow: /"])
            elif result.status in {404, 410}:
                parser.parse(["User-agent: *", "Allow: /"])
            else:
                parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
            self.robots[origin] = parser
        return self.robots[origin]

    def get(self, url: str, domains: list[str]) -> Download:
        return self._request(url, domains)
