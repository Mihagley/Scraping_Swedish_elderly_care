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



import pytest

from municipal_research.procurement import is_elderly_care


@pytest.mark.parametrize(
    "text",
    [
        "Personalen ska kunna tala, läsa och skriva svenska.",
        "Personalen ska behärska svenska.",
        "Språkkrav: nivå B2 enligt Gers ska gälla för personalen.",
        "Medarbetare ska ha kunskaper i svenska motsvarande B2 enligt gemensam europeisk referensram.",
        "Utföraren ska ha personal som kan kommunicera på svenska.",
        "Personalen ska ha godkänt betyg i svenska som andraspråk.",
    ],
)
def test_common_staff_requirements_are_explicit(text):
    assert classify_language_requirement(text).category == "explicit_requirement"


@pytest.mark.parametrize(
    "text",
    [
        "Omsorgstagare som inte talar svenska språket ska erbjudas tolk.",
        "Den enskilde har rätt till tolk om hen inte behärskar svenska.",
        "Brukare med finska som modersmål ska kunna få insatser på finska.",
    ],
)
def test_service_user_language_is_not_staff_requirement(text):
    assert classify_language_requirement(text).category != "explicit_requirement"


def test_user_context_does_not_hide_later_staff_requirement():
    text = "Omsorgstagaren har rätt till tolk. Personalen ska kunna tala och skriva svenska."
    assert classify_language_requirement(text).category == "explicit_requirement"


def test_evidence_contains_match_despite_extra_whitespace():
    text = "Avtalet   gäller\n\n\n" + "x   " * 300 + "Personalen ska ha god\n svenska."
    finding = classify_language_requirement(text)
    assert finding.category == "explicit_requirement"
    assert "god svenska" in finding.evidence.casefold()


def test_unrelated_numbers_are_not_course_levels():
    assert classify_language_requirement("Priset anges i svenska 2025 års kronor.").category == "no_evidence"


def test_elderly_care_filter():
    assert is_elderly_care({"title": "Hemtjänst enligt LOV"})
    assert is_elderly_care({"title": "Drift av vård- och omsorgsboende"})
    assert is_elderly_care({"title": "Tjänster", "cpv": "85311100-3"})
    assert not is_elderly_care({"title": "Snöröjning 2022", "cpv": "90620000"})
