import json

import httpx
import pytest
from openai import OpenAI

from municipal_research.llm import Gateway, LLMError
from municipal_research.models import Verdict


def envelope(output, status="completed"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": "test",
        "output": output,
    }


def message(content):
    return [
        {
            "type": "message",
            "id": "msg_test",
            "status": "completed",
            "role": "assistant",
            "content": content,
        }
    ]


def text_output(value):
    return message([{"type": "output_text", "text": json.dumps(value), "annotations": []}])


def gateway(config, tmp_path, audit, handler, **kwargs):
    return Gateway(
        config.llm,
        tmp_path / "cache",
        tmp_path / "run",
        audit,
        client=OpenAI(
            api_key="test-key",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        **kwargs,
    )


def test_real_sdk_strict_request_cache_and_schema(config, tmp_path, audit):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        assert payload["text"]["format"]["schema"]["additionalProperties"] is False
        assert payload["store"] is False
        assert "previous_response_id" not in payload and "conversation" not in payload
        return httpx.Response(
            200, json=envelope(text_output({"supported": True, "issues": [], "explanation": "ok"}))
        )

    live = gateway(config, tmp_path, audit, respond)
    assert live.parse("verifier", "test", "System", {"source": "data"}, Verdict).supported
    assert live.calls == 1
    replay = gateway(
        config, tmp_path, audit, lambda r: pytest.fail("Offline API request"), offline=True
    )
    assert replay.parse("verifier", "test", "System", {"source": "data"}, Verdict).supported
    assert replay.calls == 0 and len(requests) == 1
    with pytest.raises(LLMError, match="cache miss"):
        replay.parse("verifier", "changed-model", "System", {"source": "data"}, Verdict)


@pytest.mark.parametrize(
    "output,status",
    [
        (message([{"type": "refusal", "refusal": "test refusal"}]), "completed"),
        ([], "incomplete"),
        ([], "completed"),
        (text_output({"supported": "not a boolean"}), "completed"),
    ],
)
def test_fail_closed_on_bad_responses(config, tmp_path, audit, output, status):
    instance = gateway(
        config, tmp_path, audit, lambda r: httpx.Response(200, json=envelope(output, status))
    )
    with pytest.raises(LLMError):
        instance.parse("test", "test", "System", {}, Verdict)


def test_budget(config, tmp_path, audit):
    config.llm.max_calls = 1
    instance = gateway(
        config,
        tmp_path,
        audit,
        lambda r: httpx.Response(
            200, json=envelope(text_output({"supported": True, "issues": [], "explanation": "ok"}))
        ),
    )
    instance.parse("one", "test", "System", {}, Verdict)
    with pytest.raises(LLMError, match="budget"):
        instance.parse("two", "test", "System", {}, Verdict)


def test_search_uses_tool_sources_not_prose(config, tmp_path, audit):
    def respond(request):
        data = json.loads(request.content)
        assert data["tools"][0]["filters"]["allowed_domains"] == ["town.example"]
        assert data["include"] == ["web_search_call.action.sources"]
        return httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "type": "web_search_call",
                        "id": "ws_test",
                        "status": "completed",
                        "action": {
                            "type": "search",
                            "query": "policy",
                            "sources": [{"type": "url", "url": "https://town.example/real"}],
                        },
                    },
                    *message(
                        [
                            {
                                "type": "output_text",
                                "text": "https://town.example/invented",
                                "annotations": [],
                            }
                        ]
                    ),
                ]
            ),
        )

    assert gateway(config, tmp_path, audit, respond).search("policy", ["town.example"]) == [
        {"url": "https://town.example/real", "title": ""}
    ]
