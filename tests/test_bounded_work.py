"""Bounded offline regressions; no live services, crawls or paid calls."""

import time
from pathlib import Path

import httpx
import pytest

from municipal_research.budget import Budget, BudgetExhausted, bounded_call
from municipal_research.classification import Classifier
from municipal_research.config import Config, Discovery, Limits
from municipal_research.discovery import Discoverer
from municipal_research.llm import Gateway, LLMError
from municipal_research.network import Fetcher
from municipal_research.pipeline import run_pipeline
from municipal_research.storage import Audit, read_json
from test_classification import FakeGateway


def hang():
    time.sleep(30)


def test_killable_operation_deadline():
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        bounded_call(hang, (), 0.2)
    assert time.monotonic() - start < 5


def test_request_and_elapsed_limits():
    now = [0.0]
    budget = Budget(Limits(run_requests=2, organisation_requests=1, run_seconds=10, organisation_seconds=4), clock=lambda: now[0], sleep=lambda seconds: None)
    budget.begin("a")
    budget.request("robots")
    with pytest.raises(BudgetExhausted, match="organisation:request_limit"):
        budget.request("candidate")
    budget.begin("b")
    budget.request("sitemap")
    with pytest.raises(BudgetExhausted, match="run:request_limit"):
        budget.request("candidate")
    now[0] = 11
    with pytest.raises(BudgetExhausted, match="run:elapsed_time"):
        budget.timeout(600)


def test_candidate_attempts_include_failures_and_navigation(config, municipality, tmp_path, audit):
    config.discovery.provider = "crawl"
    config.discovery.max_documents = 2
    config.discovery.max_sitemaps = 0
    config.network.retries = 0
    municipality.seeds = ["https://town.example/bad"]
    budget = Budget(config.limits)
    budget.begin(municipality.id)
    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/bad":
            return httpx.Response(404)
        return httpx.Response(200, text='<html><a href="/opaque.pdf">kunskaper i svenska</a></html>', headers={"content-type": "text/html"})
    fetcher = Fetcher(config.network, tmp_path / "cache", audit, client=httpx.Client(transport=httpx.MockTransport(respond)), address_check=lambda url: None, budget=budget)
    discovery = Discoverer(config, fetcher, None, audit)
    list(discovery.documents(municipality))
    assert budget.current["attempted_retrievals"] == 2
    assert budget.current["failed_downloads"] == 1
    assert "candidate_attempt_limit" in discovery.gaps
    assert any("opaque.pdf" in row[3] for row in discovery.queue)
    assert Discovery().max_documents == 5


def test_conditional_refresh_retains_verified_body(config, tmp_path, audit):
    config.network.respect_robots = False
    config.network.interval_seconds = 0
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="body", headers={"ETag": '"version1"', "Last-Modified": "Wed, 01 Jan 2025 12:00:00 GMT"})
        assert request.headers["if-none-match"] == '"version1"'
        assert "if-modified-since" in request.headers
        return httpx.Response(304)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    first = Fetcher(config.network, tmp_path / "cache", audit, client=client, address_check=lambda url: None)
    old = first.get("https://town.example/file", ["town.example"])
    first.refresh = True
    new = first.get("https://town.example/file", ["town.example"])
    assert new.body == old.body and new.sha256 == old.sha256 and new.cache_hit


def test_light_mode_keeps_quote_check_and_requires_review(config, municipality, document, chunk, yes_decision, audit):
    config.llm.analysis_mode = "light"
    gateway = FakeGateway({"default": yes_decision})
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert len(gateway.requests) == 1
    assert result["needs_review"] and result["quotes"][0]["verified"]
    yes_decision.evidence[0].text = "Not present in the source document."
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert result["method"] == "unresolved"
    assert result["decision"]["category"] == config.research.unknown_label


def test_input_context_guard_makes_no_model_call(config, tmp_path, audit):
    config.llm.input_tokens = 256
    from municipal_research.models import Verdict
    gateway = Gateway(config.llm, tmp_path / "cache", tmp_path / "run", audit)
    with pytest.raises(LLMError, match="context budget"):
        gateway.parse("test", "test", "system", {"source": "å" * 500}, Verdict)
    assert gateway.calls == 0
    assert list((tmp_path / "run/llm").glob("*/request.json"))


def test_partial_output_and_resume_without_increasing_attempts(config, tmp_path, municipality):
    config.discovery.provider = "crawl"
    config.discovery.max_sitemaps = 0
    config.discovery.max_documents = 1
    config.network.retries = 0
    config.limits.run_requests = 1
    config.limits.organisation_requests = 10
    config.network.respect_robots = True
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text='<html><head><title>Policy</title></head><body><main>A sufficiently long readable public source with no finding.</main></body></html>', headers={"content-type": "text/html"})
    def factory(*args, **kwargs):
        return Fetcher(*args, **kwargs, client=httpx.Client(transport=httpx.MockTransport(respond)), address_check=lambda url: None)
    other = municipality.model_copy(update={"id": "other"})
    source = tmp_path / "first"
    data = run_pipeline(config, [municipality, other], source, tmp_path / "cache", collect_only=True, fetcher_factory=factory)
    assert data["progress"]["stopping_reasons"][0]["reason"] == "request_limit"
    assert data["progress"]["unprocessed_organisations"] == ["other"]
    assert (source / "results.xlsx").exists()
    sealed = (source / "manifest.json").read_bytes()
    config.limits.run_requests = 10  # An explicit new budget, not an automatic increase.
    resumed = run_pipeline(config, [municipality, other], tmp_path / "continued", tmp_path / "cache", collect_only=True, fetcher_factory=factory, resume_from=source)
    assert len(resumed["documents"]) == 2
    assert (source / "manifest.json").read_bytes() == sealed
    assert resumed["summary"][0]["counts"]["attempted_retrievals"] == 1
    assert read_json(tmp_path / "continued/manifest.json")["resume_from"] == str(source.resolve())


def test_explicit_portal_hosts_only(municipality):
    from municipal_research.network import domain_allowed
    municipality.portal_hosts = ["documents.example"]
    assert domain_allowed("https://documents.example/opaque.pdf", municipality.allowed_domains)
    assert not domain_allowed("https://untrusted.example/opaque.pdf", municipality.allowed_domains)
