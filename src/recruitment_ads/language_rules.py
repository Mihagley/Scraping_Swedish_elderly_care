"""Versioned high-recall candidates and conservative statement-level semantic rules."""

import re

VERSION = "recruitment-sv-1.2.0"
CATEGORIES = {
    "gers_b1": r"\bb\s?1\b",
    "gers_b2": r"\bb\s?2\b",
    "other_cefr_level": r"\b(?:a[12]|c[12])\b",
    "svenska_1": r"\bsvenska\s*(?:nivå\s*)?1\b",
    "svenska_som_andrasprak_1": r"\bsvenska som andraspråk\s*(?:nivå\s*)?1\b",
    "sva_1": r"\b(?:sva|sas)\s*1\b",
    "svenska_a": r"\bsvenska\s+a\b",
    "svenska_som_andrasprak_a": r"\b(?:svenska som andraspråk\s+a|sas\s*a|sva\s*a)\b",
    "sfi_a": r"\bsfi\s*[-:]?\s*(?:kurs\s*|nivå\s*)?a\b",
    "sfi_b": r"\bsfi\s*[-:]?\s*(?:kurs\s*|nivå\s*)?b\b",
    "sfi_c": r"\bsfi\s*[-:]?\s*(?:kurs\s*|nivå\s*)?c\b",
    "sfi_d": r"\bsfi\s*[-:]?\s*(?:kurs\s*|nivå\s*)?d\b",
    "sfi_unspecified": r"\bsfi\b",
    "language_test": r"\bspråk(?:test|prov)\w*|\b(?:test|prov)\s+i\s+svenska\b",
    "strong_qualitative": r"\b(?:mycket (?:goda|bra) (?:språk)?kunskaper i (?:det )?svenska|behärska[r]? (?:det )?svenska(?: språket)?|flytande svenska|obehindrat (?:på )?svenska|fullgod svenska)\b",
    "functional_oral_written": r"\bsvenska(?: språket)?[, ]*(?:(?:väl |både |i |gällande )*)(?:tal och (?:i )?skrift|skrift och tal|muntligt och skriftligt)|\b(?:tala och skriva|läsa, skriva och förstå) svenska|\buttryck(?:a|er) (?:dig|sig) (?:väl |bra )?på svenska|(?:kommunicera|uttryck\w*).{0,45}(?:muntligt och skriftligt|tal och skrift).{0,25}svenska",
    "generic_swedish_requirement": r"\bsvensktalande\b|\bsvenskkunskaper\b|\b(?:goda )?(?:språk)?kunskaper i (?:det )?svenska|\bsvenska (?:språket )?krävs",
}
FORMAL = tuple(list(CATEGORIES)[:14])
CANDIDATE = re.compile(
    r"\bsvensk\w*|\bsvensk[- ]|\b(?:sva|sas|sfi|gers|cefr)\b|\b[abc]\s?[12]\b|\bspråk(?:test|prov|stöd|utveckl)\w*",
    re.I,
)
STATUS = (
    "required",
    "preferred",
    "training_support",
    "descriptive",
    "alternative_language",
    "application_instruction",
    "uncertain",
    "irrelevant",
)


def sentences(text):
    # Offsets are always into original text; whitespace and capitalisation stay unchanged.
    return [
        (m.start(), m.end(), m.group())
        for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text, re.M)
        if m.group().strip()
    ]


def semantic(text, category, preceding=""):
    t = text.casefold()
    if category == "untyped" and re.search(
        r"svensk(?:t|a)?\s+(?:medborgar\w*|körkort|undersköterskeutbildning|legitimation)|svenska kyrkan",
        t,
    ):
        return "irrelevant", "nationality_credential_or_organisation"
    if re.search(r"ansök(?:an|ningar|ning)|cv\b|personligt brev", t) and re.search(
        r"(?:ska|skall|måste|kan|lämna|skriv|skicka).{0,50}svenska|svenska.{0,30}(?:ansök|cv)", t
    ):
        return "application_instruction", "application_language"
    if re.search(
        r"(?:svenska(?: språket)?\s+eller\s+(?:engelska|finska|danska|norska)|(?:engelska|finska|danska|norska)\s+eller\s+svenska)",
        t,
    ):
        return "alternative_language", "disjunctive_languages"
    if re.search(r"(?:inga?|inte|ej)\s+(?:ett?\s+)?krav|krävs inte|behöver inte|utan krav", t):
        return "irrelevant", "negated_requirement"
    if re.search(r"meriterande|en fördel|önskvärt|gärna|önskemål|\bbör\b", t):
        return "preferred", "preference_cue"
    if re.search(
        r"(?:vi erbjuder|du får|erbjuds|kan kombinera).{0,65}(?:svensk|språk|sfi)|språkutvecklande|sfi[- ]studerande.*välkomna",
        t,
    ):
        return "training_support", "support_cue"
    if re.search(
        r"kommer att dokumentera|dokumentation(?:en)? (?:sker|är)|(?:består av|finsktalande|svensktalande) personal|svensk- och",
        t,
    ):
        return "descriptive", "workplace_description"
    if category in ("gers_b1", "gers_b2", "other_cefr_level"):
        if re.search(r"körkort|körkortsbehörighet", t):
            return "uncertain", "cefr_or_driving_licence_review"
        if not re.search(r"svensk|\bsva\b|\bsas\b", t + " " + preceding.casefold()):
            return "irrelevant", "cefr_not_linked_to_swedish"
        if re.search(r"engelska.{0,35}\b[abc]\s?[12]\b", t):
            return "irrelevant", "cefr_other_language"
    if category == "language_test" and re.search(
        r"(?:engelsk|finsk).{0,20}(?:test|prov)|(?:test|prov).{0,20}(?:engelsk|finsk)", t
    ):
        return "irrelevant", "test_other_language"
    if category == "language_test" and not re.search(r"svensk", t + " " + preceding.casefold()):
        return "uncertain", "test_language_unspecified"
    if category == "untyped" and not re.search(
        r"tala|skriva|läsa|förstå|uttryck|kommunicer|språk|kunskap|behärsk|kan svenska", t
    ):
        return "uncertain", "no_proficiency_evidence"
    required = r"\bkrävs\b|\bkrav\b|\bmåste\b|\bbehöver du\b|\b(?:du|sökande|kandidaten)\s+(?:ska|skall|behöver|har|behärskar|kan)\b|vi (?:kräver|förutsätter)|förutsättning|ska (?:ha|kunna|genomföra|göra)|godkänt betyg|lägst|minst"
    if re.search(required, t):
        return "required", "explicit_requirement"
    if (
        preceding.strip().casefold().rstrip(":") in ("krav", "kvalifikationer", "kompetenskrav")
        and category != "untyped"
    ):
        return "required", "qualification_heading"
    return "uncertain", "no_unambiguous_entry_requirement"


def classify_language(text, extra_rules=None):
    rules = {**CATEGORIES, **(extra_rules or {})}
    spans = sentences(text)
    hits = []
    heading = ""
    for i, (start, _end, sentence) in enumerate(spans):
        clean = sentence.strip().casefold().rstrip(":")
        if clean in ("krav", "kvalifikationer", "kompetenskrav", "meriterande", "vi söker dig som"):
            heading = "preferred" if clean == "meriterande" else "required"
        elif clean in ("arbetsuppgifter", "om oss", "vi erbjuder", "övrigt", "ansökan"):
            heading = ""
        if not CANDIDATE.search(sentence):
            continue
        before = spans[i - 1][2] if i else ""
        after = " ".join(s[2] for s in spans[i + 1 : i + 3])
        # Distinct clauses avoid a preferred English clause changing a required Swedish clause.
        # Split coordinated independent statements, preserving original character offsets.
        boundaries = (
            [0]
            + [
                m.end()
                for m in re.finditer(
                    r";|\bmen\b|,\s*(?=(?:engelska|finska|norska|danska)\b)", sentence, re.I
                )
            ]
            + [len(sentence)]
        )
        clauses = [
            re.match(r"[\s\S]+", sentence[a:b])
            for a, b in zip(boundaries, boundaries[1:], strict=False)
        ]
        for offset, clause in zip(boundaries, clauses, strict=False):
            if clause is None:
                continue
            s = clause.group()
            if not CANDIDATE.search(s):
                continue
            found = [
                (category, m)
                for category, pattern in rules.items()
                for m in re.finditer(pattern, s, re.I)
            ]
            if any(c.startswith("sfi_") and c != "sfi_unspecified" for c, _ in found):
                found = [(c, m) for c, m in found if c != "sfi_unspecified"]
            if not found:
                found = [("untyped", CANDIDATE.search(s))]
            for category, m in found:
                status, semantic_rule = semantic(s, category, before)
                if (
                    status == "uncertain"
                    and semantic_rule == "no_unambiguous_entry_requirement"
                    and heading
                    and category != "untyped"
                    and re.match(r"\s*[•*\-]", s)
                ):
                    status, semantic_rule = heading, "qualification_list_heading"
                hit_start = start + offset + m.start()
                hit_end = start + offset + m.end()
                hits.append(
                    {
                        "category": category,
                        "status": status,
                        "rule_id": f"{category}:{semantic_rule}",
                        "matched_phrase": text[hit_start:hit_end],
                        "matched_sentence": sentence,
                        "context_before": before,
                        "context_after": after,
                        "start": hit_start,
                        "end": hit_end,
                    }
                )
    return hits
