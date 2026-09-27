from municipal_research.models import Chunk
from municipal_research.triage import score_chunk, select_chunks


def chunk(identifier: str, document_id: str, start: int, text: str) -> Chunk:
    return Chunk(
        id=identifier,
        document_id=document_id,
        start=start,
        end=start + len(text),
        text=text,
    )


def test_selects_explicit_elderly_care_language_requirement():
    item = chunk(
        "doc:0:100",
        "doc",
        0,
        "Inom äldreomsorgen ska nyanställda uppfylla språkkrav motsvarande Svenska 1.",
    )
    document = {"id": "doc", "title": "Riktlinje", "url": "https://example.se/riktlinje"}
    row = score_chunk(item, document)
    assert row["selected_direct"] is True
    assert "language_requirement" in row["reasons"]
    assert "elderly_care" in row["reasons"]


def test_does_not_select_generic_swedish_without_policy_context():
    item = chunk(
        "doc:0:100", "doc", 0, "Webbplatsen innehåller information på svenska och engelska."
    )
    document = {"id": "doc", "title": "Kontakta kommunen", "url": "https://example.se/kontakt"}
    row = score_chunk(item, document)
    assert row["selected_direct"] is False


def test_keeps_adjacent_context_and_marks_budget_limit():
    chunks = [
        chunk("doc:0:20", "doc", 0, "Beslut 2023-05-01."),
        chunk(
            "doc:20:80",
            "doc",
            20,
            "Äldreomsorgen inför språkkrav för nyanställda med Svenska 1.",
        ),
        chunk("doc:80:120", "doc", 80, "Kravet gäller från den 1 september 2023."),
    ]
    documents = {
        "doc": {
            "id": "doc",
            "title": "Protokoll äldreomsorg",
            "url": "https://example.se/protokoll",
        }
    }
    selected, rows, limited = select_chunks(chunks, documents, max_selected=2, context_neighbors=1)
    assert len(selected) == 2
    assert limited is True
    assert any("adjacent_context" in row["reasons"] for row in rows)
    assert any("triage_budget_excluded" in row["reasons"] for row in rows)
