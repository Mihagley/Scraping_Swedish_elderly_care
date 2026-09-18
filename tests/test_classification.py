from municipal_research.classification import Classifier
from municipal_research.models import Verdict


class FakeGateway:
    def __init__(self, responses):
        self.responses, self.requests = responses, []

    def parse(self, role, model, system, payload, schema):
        self.requests.append((role, payload))
        response = self.responses.get(role, self.responses.get("default"))
        if isinstance(response, Exception):
            raise response
        return response


def test_consensus_has_blind_passes(config, municipality, document, chunk, yes_decision, audit):
    gateway = FakeGateway(
        {
            "default": yes_decision,
            "verify_consensus": Verdict(supported=True, issues=[], explanation="Supported"),
        }
    )
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert result["method"] == "consensus_verified"
    assert not result["needs_review"]
    assert len(gateway.requests) == 4
    for _role, payload in gateway.requests[:3]:
        assert "candidate_audit" not in payload and "proposed_decision" not in payload


def test_disagreement_is_adjudicated(config, municipality, document, chunk, yes_decision, audit):
    different = yes_decision.model_copy(update={"category": "Pilot"})
    gateway = FakeGateway(
        {
            "default": yes_decision,
            "classify_2": different,
            "verify_adjudication": Verdict(supported=True, issues=[], explanation="Supported"),
        }
    )
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert result["method"] == "adjudicated_verified"
    assert result["decision"]["category"] == "Yes"
    assert not result["agreement"]


def test_semantic_rejection_cannot_escape(
    config, municipality, document, chunk, yes_decision, audit
):
    rejected = Verdict(supported=False, issues=["Misattributed timing"], explanation="Unsupported")
    gateway = FakeGateway(
        {"default": yes_decision, "verify_consensus": rejected, "verify_adjudication": rejected}
    )
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert result["decision"]["category"] == "NoEvidence"
    assert result["needs_review"] and result["method"] == "unresolved"


def test_invented_quote_never_supports_final(
    config, municipality, document, chunk, yes_decision, audit
):
    yes_decision.evidence[0].text = "This sentence is invented and absent."
    gateway = FakeGateway({"default": yes_decision})
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert result["method"] == "unresolved" and result["quotes"] == []
    assert all(not p["quotes"][0]["verified"] for p in result["passes"] if "quotes" in p)


def test_all_api_failures_are_unknown(config, municipality, document, chunk, audit):
    gateway = FakeGateway({"default": RuntimeError("API unavailable")})
    result = Classifier(config, gateway, audit).classify(municipality, document, chunk)
    assert len(result["errors"]) == 3
    assert result["decision"]["category"] != "No"
    assert result["needs_review"]
