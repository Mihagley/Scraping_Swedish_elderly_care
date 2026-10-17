import json
from pathlib import Path

import httpx
import pytest

from municipal_research.classification import Classifier
from municipal_research.config import LLM
from municipal_research.demo import run_demo
from municipal_research.llm import Gateway, LLMError, check_ollama
from municipal_research.models import Verdict
from municipal_research.storage import read_json

MODEL = "llama3.1:8b"
GOOD = {"supported": True, "issues": [], "explanation": "Belagt i källan."}


def local_config(**updates):
    return LLM(
        **{
            "provider": "ollama",
            "models": [MODEL],
            "verifier_model": MODEL,
            "adjudicator_model": MODEL,
            "interval_seconds": 0,
            **updates,
        }
    )


def envelope(content=GOOD, **updates):
    return {
        "model": MODEL,
        "message": {"role": "assistant", "content": json.dumps(content)},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 123,
        "eval_count": 45,
        **updates,
    }


def gateway(tmp_path, audit, handler, *, settings=None, **kwargs):
    return Gateway(
        settings or local_config(),
        tmp_path / "cache",
        tmp_path / "run",
        audit,
        local_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_native_schema_audit_and_offline_replay(tmp_path, audit, monkeypatch):
    requests = []
    monkeypatch.setattr("municipal_research.llm.OpenAI", lambda **kw: pytest.fail("Cloud call"))

    def respond(request):
        assert str(request.url) == "http://localhost:11434/api/chat"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["format"] == Verdict.model_json_schema()
        assert body["options"] == {"num_ctx": 32768, "num_predict": 6000, "temperature": 0}
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        assert "previous_response_id" not in body
        requests.append(body)
        return httpx.Response(200, json=envelope())

    instance = gateway(tmp_path, audit, respond)
    assert instance.parse("verify", MODEL, "System", {"source": "Svensk text"}, Verdict).supported
    assert instance.calls == 1 and instance.client is None
    saved = read_json(next((tmp_path / "run/llm").glob("*/response.json")))
    assert saved["response"] == envelope()
    event = json.loads(audit.path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["provider"] == "ollama"
    assert event["usage"] == {"input_tokens": 123, "output_tokens": 45}
    replay = gateway(tmp_path, audit, lambda r: pytest.fail("Offline HTTP"), offline=True)
    assert replay.parse("verify", MODEL, "System", {"source": "Svensk text"}, Verdict).supported
    assert replay.calls == 0 and len(requests) == 1
    changed = gateway(
        tmp_path,
        audit,
        lambda r: pytest.fail("Offline HTTP"),
        offline=True,
        settings=local_config(ollama_base_url="http://127.0.0.1:11434"),
    )
    with pytest.raises(LLMError, match="cache miss"):
        changed.parse("verify", MODEL, "System", {"source": "Svensk text"}, Verdict)


@pytest.mark.parametrize(
    "response",
    [
        envelope(done=False),
        envelope(done_reason="length"),
        envelope(done_reason=None),
        envelope(message={"role": "assistant", "content": "not JSON"}),
        envelope(message={"role": "assistant", "content": ""}),
        envelope(message={"role": "user", "content": json.dumps(GOOD)}),
        envelope({**GOOD, "supported": "true"}),
        envelope({**GOOD, "unexpected": "field"}),
    ],
)
def test_incomplete_or_invalid_output_abstains(tmp_path, audit, response):
    instance = gateway(tmp_path, audit, lambda r: httpx.Response(200, json=response))
    with pytest.raises(LLMError):
        instance.parse("verify", MODEL, "System", {}, Verdict)
    saved = read_json(next((tmp_path / "run/llm").glob("*/response.json")))
    assert saved["response"] == response


def test_context_and_endpoint_separate_caches(tmp_path, audit):
    def handler(request):
        return httpx.Response(200, json=envelope())

    first = gateway(tmp_path, audit, handler)
    first.parse("one", MODEL, "System", {}, Verdict)
    other = gateway(tmp_path, audit, handler, settings=local_config(ollama_num_ctx=65536))
    other.parse("one", MODEL, "System", {}, Verdict)
    assert other.calls == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 2


def test_budget_and_http_errors(tmp_path, audit, monkeypatch):
    monkeypatch.setattr("municipal_research.llm.OpenAI", lambda **kw: pytest.fail("Cloud call"))
    instance = gateway(
        tmp_path,
        audit,
        lambda r: httpx.Response(404, json={"error": "model not found"}),
        settings=local_config(max_calls=1),
    )
    with pytest.raises(LLMError, match="Ollama call failed"):
        instance.parse("one", MODEL, "System", {}, Verdict)
    assert next((tmp_path / "run/llm").glob("*/error.json")).exists()
    with pytest.raises(LLMError, match="budget"):
        instance.parse("two", MODEL, "System", {}, Verdict)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com",
        "http://localhost.evil:11434",
        "http://127.0.0.1:11434/v1",
        "http://name:secret@localhost:11434",
        "ftp://localhost",
        "http://localhost:bad",
        "http://localhost:11434?token=abc",
    ],
)
def test_only_explicit_local_origin_is_allowed(url):
    with pytest.raises(ValueError):
        local_config(ollama_base_url=url)


def test_local_parameter_validation():
    for updates in [
        {"ollama_num_ctx": 2048},
        {"models": ["llama3.1:8b-cloud"]},
        {"models": [""]},
        {"reasoning_effort": "high"},
    ]:
        with pytest.raises(ValueError):
            local_config(**updates)
    assert local_config(ollama_base_url="http://[::1]:11434/").ollama_base_url.endswith("11434")


def test_connection_check_does_not_generate_or_download():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": MODEL}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    check_ollama(local_config(), client=client)
    assert len(requests) == 1
    with pytest.raises(LLMError, match="Modellen saknas"):
        check_ollama(local_config(models=["missing:8b"]), client=client)


@pytest.mark.parametrize("fabricated", [False, True])
def test_local_classification_preserves_quote_verification(
    config,
    tmp_path,
    audit,
    municipality,
    document,
    chunk,
    yes_decision,
    monkeypatch,
    fabricated,
):
    monkeypatch.setattr("municipal_research.llm.OpenAI", lambda **kw: pytest.fail("Cloud call"))
    config.llm = local_config()
    decision = yes_decision.model_dump()
    if fabricated:
        decision["evidence"][0]["text"] = "Detta citat finns inte i källtexten."

    def respond(request):
        body = json.loads(request.content)
        content = GOOD if body["format"]["title"] == "Verdict" else decision
        assert body["model"] == MODEL
        return httpx.Response(200, json=envelope(content))

    instance = gateway(tmp_path, audit, respond, settings=config.llm)
    record = Classifier(config, instance, audit).classify(municipality, document, chunk)
    assert record["decision"]["category"] == ("NoEvidence" if fabricated else "Yes")
    assert record["needs_review"] is fabricated
    assert instance.client is None


def test_demo_stays_mocked_when_given_local_config(config, tmp_path):
    config.llm = local_config()
    data = run_demo(config, tmp_path / "demo", tmp_path / "cache")
    assert not data["errors"]


@pytest.mark.parametrize("thinking", [False, True, None])
def test_qwen_thinking_is_separate_from_final_json(tmp_path, audit, thinking):
    model = "qwen3.5:9b"
    settings = local_config(
        models=[model],
        verifier_model=model,
        adjudicator_model=model,
        ollama_num_ctx=16384,
        max_output_tokens=2000,
        ollama_think=thinking,
    )

    def respond(request):
        body = json.loads(request.content)
        assert body["options"]["num_ctx"] == 16384
        assert body["options"]["num_predict"] == 2000
        if thinking is None:
            assert "think" not in body
        else:
            assert body["think"] is thinking
        return httpx.Response(
            200,
            json=envelope(
                model=model,
                message={
                    "role": "assistant",
                    "thinking": json.dumps({**GOOD, "supported": False}),
                    "content": json.dumps(GOOD),
                },
            ),
        )

    instance = gateway(tmp_path, audit, respond, settings=settings)
    assert instance.parse("verify", model, "System", {}, Verdict).supported
    saved = read_json(next((tmp_path / "run/llm").glob("*/response.json")))
    assert "thinking" in saved["response"]["message"]


def test_qwen_thinking_cannot_replace_missing_final_message(tmp_path, audit):
    response = envelope(message={"role": "assistant", "thinking": json.dumps(GOOD), "content": ""})
    instance = gateway(tmp_path, audit, lambda request: httpx.Response(200, json=response))
    with pytest.raises(LLMError, match="no structured output"):
        instance.parse("verify", MODEL, "System", {}, Verdict)


def test_qwen_context_guard_prevents_oversized_local_call(tmp_path, audit):
    settings = local_config(
        ollama_num_ctx=16384,
        max_output_tokens=2000,
        input_tokens=200000,
        ollama_think=False,
    )
    instance = gateway(
        tmp_path,
        audit,
        lambda request: pytest.fail("Oversized request reached Ollama"),
        settings=settings,
    )
    with pytest.raises(LLMError, match="context budget"):
        instance.parse("verify", MODEL, "System", {"source": "漢" * 6000}, Verdict)
    assert instance.calls == 0


def test_qwen_example_preserves_small_candidate_and_call_caps():
    from municipal_research.config import load_config

    example = Path(__file__).parents[1] / "examples/local_qwen.yaml"
    config = load_config(example)
    assert config.llm.models == ["qwen3.5:9b"]
    assert config.llm.ollama_num_ctx == 16384
    assert config.llm.ollama_think is False
    assert config.llm.max_output_tokens == 2000
    assert config.llm.max_calls == 50
    assert config.discovery.max_documents == 5
    assert config.extraction.chunk_chars == 3000
