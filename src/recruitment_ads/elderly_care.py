"""Conservative context classification with reviewable positive and negative evidence."""

import re

POSITIVE = r"äldreomsorg\w*|äldreboende\w*|särskil(?:t|da) boende\w*|säbo|hemtjänst\w*|hemvård\w*|vård[- ]+och omsorgsboende\w*|vårdboende\w*|demensboende\w*|korttidsboende\w*|nattpatrull\w*"
NEGATIVE = r"lss|personlig(?:a)? assistan\w*|funktionsstöd\w*|funktionsnedsätt\w*|gruppboende\w*|serviceboende\w*|socialpsykiatri\w*|psykiatri\w*|barn|ungdom\w*|elev\w*"
ROLE = r"arbeta\w*|arbete\w*|tjänst\w*|söker|rekryter\w*|omsorg|vård|hjälp\w*|stöd\w*|avdelning\w*|enhet\w*|boende\w*|brukare\w*|underskötersk\w*|vårdbiträd\w*"


def context(title, text, code, rules=None):
    rules = rules or {}
    pos = re.compile(r"\b(?:" + rules.get("positive", POSITIVE) + r")\b", re.I)
    neg = re.compile(r"\b(?:" + rules.get("negative", NEGATIVE) + r")\b", re.I)
    evidence, exclusions = [], []
    for i, sentence in enumerate([title] + re.split(r"[.!?\n]+", text)):
        role = i == 0 or re.search(ROLE, sentence, re.I)
        # Boilerplate age of applicants or a municipality's general service list is not job context.
        boilerplate = re.search(
            r"kommunen (?:har|erbjuder)|från förskola|alla åldrar|år eller äldre|äldre än \d|belastningsregist",
            sentence,
            re.I,
        )
        if not role or boilerplate:
            continue
        if pos.search(sentence) or re.search(
            r"(?:vård|omsorg|hjälp|stöd).{0,45}\bäldre\b|\bäldre\b.{0,35}(?:vård|omsorg)",
            sentence,
            re.I,
        ):
            evidence.append(sentence.strip())
        if neg.search(sentence):
            exclusions.append(sentence.strip())
    if evidence and exclusions:
        status = "mixed"
    elif exclusions:
        status = "no"
    elif evidence or code == "5321":
        status = "yes"
    else:
        status = "uncertain"
    return {
        "elderly_care_context": status,
        "elderly_context_evidence": evidence,
        "exclusion_context_evidence": exclusions,
        "elderly_context_basis": "text"
        if evidence
        else "core_ssyk"
        if status == "yes"
        else "review",
    }
