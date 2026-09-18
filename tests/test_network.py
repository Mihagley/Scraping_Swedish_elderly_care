import httpx
import pytest

from municipal_research.config import Network
from municipal_research.network import Fetcher, FetchError, Pacer, domain_allowed, normalize_url


def fetcher(tmp_path, audit, handler, **kwargs):
    return Fetcher(
        Network(interval_seconds=0, retries=1),
        tmp_path / "cache",
        audit,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        address_check=lambda url: None,
        sleep=lambda seconds: None,
        **kwargs,
    )


def test_allowlist_boundary():
    assert domain_allowed("https://www.town.se/a", ["town.se"])
    assert not domain_allowed("https://town.se.evil.example/a", ["town.se"])
    assert not domain_allowed("https://faketown.se", ["town.se"])
    assert normalize_url("https://TOWN.se/a?q=1#fragment") == "https://town.se/a?q=1"


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://name:secret@town.se", "https://town.se:444/a"]
)
def test_reject_unsafe_urls(url):
    with pytest.raises(FetchError):
        normalize_url(url)


def test_retry_cache_and_offline_integrity(tmp_path, audit):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if calls.count(str(request.url)) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, content=b"saved", headers={"content-type": "text/html"})

    first = fetcher(tmp_path, audit, respond)
    result = first.get("https://town.example/a", ["town.example"])
    assert result.body == b"saved" and not result.cache_hit
    replay = fetcher(
        tmp_path, audit, lambda request: pytest.fail("offline network call"), offline=True
    )
    assert replay.get("https://town.example/a", ["town.example"]).cache_hit
    (tmp_path / "cache/bodies" / result.sha256).write_bytes(b"tampered")
    with pytest.raises(FetchError, match="integrity"):
        replay.get("https://town.example/a", ["town.example"])


def test_robots_disallow_and_oversize(tmp_path, audit):
    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, content=b"a" * 100)

    client = fetcher(tmp_path, audit, respond)
    with pytest.raises(FetchError, match="disallows"):
        client.get("https://town.example/private", ["town.example"])
    client.config.max_bytes = 50
    with pytest.raises(FetchError, match="max_bytes"):
        client.get("https://town.example/big", ["town.example"])


def test_cross_domain_redirect_rejected(tmp_path, audit):
    calls = []

    def respond(request):
        calls.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://outside.example/file"})

    with pytest.raises(FetchError, match="allowlist"):
        fetcher(tmp_path, audit, respond).get("https://town.example/a", ["town.example"])
    assert "outside.example" not in calls


def test_offline_miss(tmp_path, audit):
    with pytest.raises(FetchError, match="Offline"):
        fetcher(tmp_path, audit, lambda req: pytest.fail(), offline=True).get(
            "https://town.example/uncached", ["town.example"]
        )


def test_pacer_spacing_per_host():
    now, waits = [0.0], []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    pacer = Pacer(2, clock=lambda: now[0], sleep=sleep)
    pacer.wait("a")
    pacer.wait("b")
    pacer.wait("a")
    pacer.wait("a", 3)
    assert waits == [2, 3]
