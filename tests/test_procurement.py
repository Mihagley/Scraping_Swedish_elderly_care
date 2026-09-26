from municipal_research.procurement import aggregate_by_year, classify_language_requirement, normalize_notice


def test_explicit_swedish_requirement_keeps_evidence():
    finding = classify_language_requirement("Personalen ska ha god svenska i tal och skrift.")
    assert finding.category == "explicit_requirement"
    assert "god svenska" in finding.evidence.casefold()


def test_missing_document_is_not_no_evidence():
    assert classify_language_requirement("").category == "missing_document"


def test_normalization_and_year_aggregation():
    notice = normalize_notice({"publication-date": "2022-04-01", "publication-number": "X1", "municipality": "Test", "document_text": "Krav på svenska."}, source="uhm")
    assert notice.year == 2022
    assert aggregate_by_year([notice])[0]["n_explicit_swedish"] == 1

