"""Explicitly synthetic fixtures. No network or real model calls are made."""

from __future__ import annotations

import io
import json

import httpx
from openai import OpenAI

from .classification import abstention
from .config import Config, Municipality
from .llm import Gateway
from .models import Attribute, Decision, Quote
from .network import Fetcher
from .pipeline import run_pipeline
from .storage import canonical

YES_QUOTE = (
    "Från och med 1 september 2023 måste nyanställda i äldreomsorgen visa svenska på nivå B1."
)
PILOT_QUOTE = "Under 2024 prövades ett krav på svenska på nivå B1 i en pilot inom äldreomsorgen."


def pdf_fixture() -> bytes:
    try:
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as error:
        raise RuntimeError(
            "The synthetic demo needs the dev extra: pip install -e '.[dev]'"
        ) from error
    buffer = io.BytesIO()
    canvas = Canvas(buffer, invariant=1)
    canvas.setTitle("SYNTHETIC EXAMPLE - Demoorten pilot")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(40, 780, "SYNTHETIC EXAMPLE. This is not real municipality evidence.")
    canvas.drawString(40, 755, PILOT_QUOTE)
    canvas.showPage()
    canvas.drawString(
        40, 780, "SYNTHETIC EXAMPLE. Page two contains no additional research findings."
    )
    canvas.save()
    return buffer.getvalue()


def fixture_sites(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    if request.url.path == "/policy.pdf":
        return httpx.Response(
            200, content=pdf_fixture(), headers={"content-type": "application/pdf"}
        )
    if request.url.path == "/policy.html":
        return httpx.Response(
            200,
            text=f"<html><head><meta charset='utf-8'><title>Synthetic policy</title></head><body><main><h1>SYNTHETIC TEST FIXTURE</h1><p>{YES_QUOTE}</p><p>This invented policy is only for exercising the pipeline.</p></main></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    return httpx.Response(
        200,
        text="<html><head><title>Synthetic home</title></head><body><main><p>Synthetic municipal demonstration website with no substantive policy facts.</p></main></body></html>",
        headers={"content-type": "text/html"},
    )


def fixture_api(config: Config):
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["input"][-1]["content"])
        schema_name = body["text"]["format"]["name"]
        if schema_name == "Verdict":
            output = {
                "supported": True,
                "issues": [],
                "explanation": "Synthetic verifier fixture, not an actual model assessment.",
            }
        else:
            text = payload["source_text"]
            quote = YES_QUOTE if YES_QUOTE in text else PILOT_QUOTE if PILOT_QUOTE in text else None
            if not quote or payload.get("independent_pass") == 2:
                decision = abstention(config, "Synthetic no-evidence/disagreement fixture.")
            else:
                decision = Decision(
                    category="Yes" if quote == YES_QUOTE else "Pilot",
                    rationale="Synthetic fixture establishes the stated pre-cutoff situation.",
                    temporal_relation="before_cutoff",
                    effective_date="2023-09-01" if quote == YES_QUOTE else None,
                    scope=None,
                    attributes=[Attribute(name="language_level", value="B1", quote_indices=[0])],
                    evidence=[
                        Quote(text=quote, purpose="finding"),
                        Quote(text=quote, purpose="timing"),
                    ],
                )
            output = decision.model_dump(mode="json")
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": canonical(output), "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )

    return respond


def run_demo(config: Config, run, cache, *, offline=False):
    if (
        not {"Yes", "Pilot"}.issubset(config.research.labels)
        or "language_level" not in config.research.fields
    ):
        raise ValueError("The synthetic demo requires the language-requirements example codebook")
    config = config.model_copy(deep=True)
    config.discovery.provider = "crawl"
    config.discovery.max_sitemaps = 0
    config.discovery.max_depth = 1
    config.network.interval_seconds = 0
    config.llm.interval_seconds = 0
    municipalities = [
        Municipality(
            id="demo_yes",
            name="Exempelköping (synthetic)",
            domains=["yes.example"],
            seeds=["https://yes.example/policy.html"],
        ),
        Municipality(
            id="demo_pilot",
            name="Demoorten (synthetic)",
            domains=["pilot.example"],
            seeds=["https://pilot.example/policy.pdf"],
        ),
    ]

    def fetcher(*args, **kwargs):
        return Fetcher(
            *args,
            **kwargs,
            client=httpx.Client(transport=httpx.MockTransport(fixture_sites)),
            address_check=lambda url: None,
        )

    def gateway(*args, **kwargs):
        client = OpenAI(
            api_key="synthetic-not-a-real-key",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(fixture_api(config))),
        )
        return Gateway(*args, **kwargs, client=client)

    return run_pipeline(
        config,
        municipalities,
        run,
        cache,
        mode="SYNTHETIC_DEMO_NO_REAL_FINDINGS",
        offline=offline,
        fetcher_factory=fetcher,
        gateway_factory=gateway,
    )
