from pathlib import Path

import pytest

from municipal_research.config import Municipality, load_config
from municipal_research.demo import YES_QUOTE
from municipal_research.models import Chunk, Decision, Document, Page, Quote
from municipal_research.storage import Audit, digest


@pytest.fixture
def config():
    value = load_config(Path(__file__).parents[1] / "examples/language_requirements.yaml")
    value.network.interval_seconds = 0
    value.llm.interval_seconds = 0
    return value


@pytest.fixture
def municipality():
    return Municipality(id="demo", name="Example", domains=["town.example"])


@pytest.fixture
def audit(tmp_path):
    return Audit(tmp_path / "audit.jsonl")


@pytest.fixture
def document():
    text = "Syntetisk källa\n" + YES_QUOTE + "\n" + YES_QUOTE
    return Document(
        id="doc",
        municipality_id="demo",
        url="https://town.example/policy",
        requested_url="https://town.example/policy",
        retrieved_at="2026-09-16T00:00:00Z",
        raw_sha256=digest(b"raw"),
        text_sha256=digest(text),
        raw_path="source.html",
        text_path="text.txt",
        numbered_path="text.lines.txt",
        media_type="html",
        title="Example",
        text=text,
        pages=[Page(page=None, start=0, end=len(text))],
        warnings=[],
        extractor="test",
    )


@pytest.fixture
def chunk(document):
    return Chunk(
        id="doc:0", document_id=document.id, start=0, end=len(document.text), text=document.text
    )


@pytest.fixture
def yes_decision():
    return Decision(
        category="Yes",
        rationale="Explicit pre-cutoff requirement in this fixture.",
        temporal_relation="before_cutoff",
        effective_date="2023-09-01",
        scope=None,
        attributes=[],
        evidence=[
            Quote(text=YES_QUOTE, purpose="finding"),
            Quote(text=YES_QUOTE, purpose="timing"),
        ],
    )
