import pytest
from pydantic import ValidationError

from municipal_research.demo import YES_QUOTE
from municipal_research.models import Attribute, Chunk, Page, Quote, decision_schema
from municipal_research.verification import latest_date, locate_quote, verify_decision


def test_all_exact_occurrences_and_unicode_offsets(document, chunk):
    locations = locate_quote(YES_QUOTE, document, chunk)
    assert len(locations) == 2
    assert [p.line_start for p in locations] == [2, 3]
    for location in locations:
        assert document.text[location.start : location.end] == YES_QUOTE
        assert location.end - location.start == len(YES_QUOTE)
        assert location.page_start is None


def test_whitespace_matching_and_pdf_pages(document):
    text = "En svensk\nregel gÃ¤ller.\n\f\nFrÃ¥n 2024 gÃ¤ller kravet."
    document.text = text
    boundary = text.index("FrÃ¥n")
    document.pages = [
        Page(page=1, start=0, end=boundary - 3),
        Page(page=2, start=boundary, end=len(text)),
    ]
    chunk = Chunk(id="c", document_id="doc", start=0, end=len(text), text=text)
    match = locate_quote("En svensk regel gÃ¤ller.", document, chunk)[0]
    assert match.match == "whitespace"
    assert match.matched_text == "En svensk\nregel gÃ¤ller."
    assert match.page_start == 1 and match.page_char_start == 0
    second = locate_quote("FrÃ¥n 2024 gÃ¤ller kravet.", document, chunk)[0]
    assert second.page_start == 2 and second.page_char_start == 0


@pytest.mark.parametrize(
    "quote", ["Invented text not in source", "", "FRÃ…N OCH MED", "FrÃ¥n ... B1."]
)
def test_no_fuzzy_or_empty_quotes(quote, document, chunk):
    assert locate_quote(quote, document, chunk) == []


def test_match_must_be_in_supplied_chunk(document):
    chunk = Chunk(id="c", document_id="doc", start=0, end=10, text=document.text[:10])
    assert locate_quote(YES_QUOTE, document, chunk) == []


def test_success_and_temporal_cutoff(config, document, chunk, yes_decision):
    checks, issues = verify_decision(yes_decision, document, chunk, config.research)
    assert not issues and all(q.verified for q in checks)
    yes_decision.effective_date = "2025-01-01"
    assert any(
        "before cutoff" in i
        for i in verify_decision(yes_decision, document, chunk, config.research)[1]
    )


def test_no_missing_citations_or_unknown_attributes(config, document, chunk, yes_decision):
    yes_decision.scope = "All staff"
    yes_decision.evidence = [Quote(text=YES_QUOTE, purpose="finding")]
    yes_decision.attributes = [Attribute(name="invented", value="B2", quote_indices=[999])]
    issues = verify_decision(yes_decision, document, chunk, config.research)[1]
    assert any("timing" in issue for issue in issues)
    assert any("Scope" in issue for issue in issues)
    assert any("Unknown" in issue for issue in issues)
    assert any("Attribute lacks" in issue for issue in issues)


@pytest.mark.parametrize(
    "value,expected",
    [("2024", "2024-12-31"), ("2024-02", "2024-02-29"), ("2023-09-01", "2023-09-01")],
)
def test_partial_dates(value, expected):
    assert latest_date(value).isoformat() == expected


@pytest.mark.parametrize("value", ["2024-13", "2024-02-31", "2024-ish", "00"])
def test_invalid_dates(value):
    with pytest.raises(ValueError):
        latest_date(value)


def test_dynamic_api_enum(config, yes_decision):
    schema = decision_schema(config.research)
    assert schema.model_json_schema()["properties"]["category"]["enum"] == list(
        config.research.labels
    )
    with pytest.raises(ValidationError):
        schema.model_validate({**yes_decision.model_dump(), "category": "invented"})
