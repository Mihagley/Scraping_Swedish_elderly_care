from __future__ import annotations

from pathlib import Path

import httpx
from openai import OpenAI
from pydantic import BaseModel

from .config import LLM
from .network import Pacer
from .storage import Audit, canonical, digest, read_json, write_json

PROMPT_VERSION = "research-v1"


class LLMError(RuntimeError):
    pass


class Gateway:
    """OpenAI Responses/Ollama adapter with strict parsing and disk replay.

    SDK handles transient API retries (including Retry-After). The cache key includes
    the full prompt, schema, model, role/pass identity, and request settings.
    """

    def __init__(
        self,
        config: LLM,
        cache: Path,
        run: Path,
        audit: Audit,
        *,
        offline=False,
        refresh=False,
        client=None,
        local_client=None,
    ):
        self.config, self.cache, self.run, self.audit = config, cache, run, audit
        self.offline, self.refresh, self.client = offline, refresh, client
        self.local_client = local_client
        self.calls = 0
        self.pacer = Pacer(config.interval_seconds)

    def close(self):
        if self.client is not None:
            self.client.close()
        if self.local_client is not None:
            self.local_client.close()

    def _call(self, role: str, model: str, messages: list[dict], schema=None, **extras):
        local = schema is not None and self.config.provider == "ollama"
        request = {
            "model": model,
            "input": messages,
            "store": False,
            "max_output_tokens": self.config.max_output_tokens,
            **extras,
        }
        if local:
            request = {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
                "options": {"temperature": 0, "num_ctx": self.config.ollama_num_ctx,
                            "num_predict": self.config.max_output_tokens},
            }
        if self.config.reasoning_effort and not local:
            request["reasoning"] = {"effort": self.config.reasoning_effort}
        identity = {
            "version": PROMPT_VERSION,
            "role": role,
            "request": request,
            "schema": schema.model_json_schema() if schema else None,
        }
        if local:
            identity.update(provider="ollama", endpoint=self.config.ollama_base_url)
        key = digest(canonical(identity))
        cache_file = self.cache / (key + ".json")
        run_folder = self.run / "llm" / key
        write_json(run_folder / "request.json", identity)
        cached = cache_file.exists() and (self.offline or not self.refresh)
        if cached:
            record = read_json(cache_file)
            if record["key"] != key or digest(canonical(record["identity"])) != key:
                raise LLMError("LLM cache identity mismatch")
            if digest(canonical(record["response"])) != record["response_sha256"]:
                raise LLMError("LLM response cache integrity check failed")
        else:
            if self.offline:
                raise LLMError("Offline LLM cache miss: " + key)
            if self.calls >= self.config.max_calls:
                raise LLMError("Configured API call budget exhausted")
            if not local and self.client is None:
                self.client = OpenAI(
                    timeout=self.config.timeout_seconds, max_retries=self.config.max_retries
                )
            self.pacer.wait("ollama" if local else "openai")
            self.calls += 1
            try:
                if local:
                    if self.local_client is None:
                        self.local_client = httpx.Client(
                            timeout=self.config.timeout_seconds, trust_env=False,
                            transport=httpx.HTTPTransport(retries=self.config.max_retries),
                        )
                    response = self.local_client.post(
                        self.config.ollama_base_url + "/api/chat", json=request,
                        timeout=self.config.timeout_seconds,
                    )
                    response.raise_for_status()
                    raw = response.json()
                    if not isinstance(raw, dict):
                        raise LLMError("Ollama returned a non-object response")
                    headers = response.headers
                elif schema:
                    response = self.client.responses.with_raw_response.parse(
                        **request, text_format=schema
                    )
                else:
                    response = self.client.responses.with_raw_response.create(**request)
                # Preserve API JSON before parsing. This also keeps invalid structured
                # outputs auditable and avoids reserializing SDK union/generic models.
                if not local:
                    raw = response.http_response.json()
                    headers = response.headers
                # Save refusals and incomplete responses too; never reinterpret them as negative findings.
                record = {
                    "key": key,
                    "identity": identity,
                    "response": raw,
                    "response_sha256": digest(canonical(raw)),
                    "request_id": headers.get("x-request-id"),
                }
                write_json(cache_file, record)
            except Exception as error:
                self.audit.emit("llm_error", role=role, key=key, error=type(error).__name__)
                write_json(
                    run_folder / "error.json",
                    {
                        "type": type(error).__name__,
                        "request_id": getattr(error, "request_id", None),
                    },
                )
                provider = "Ollama" if local else "OpenAI"
                raise LLMError(f"{provider} call failed ({type(error).__name__}); see audit") from error
        write_json(run_folder / "response.json", record)
        raw = record["response"]
        usage = raw.get("usage")
        status = raw.get("status")
        if local:
            usage = {"input_tokens": raw.get("prompt_eval_count"), "output_tokens": raw.get("eval_count")}
            status = "completed" if raw.get("done") is True and raw.get("done_reason") == "stop" else "incomplete"
        self.audit.emit(
            "llm",
            provider="ollama" if local else "openai",
            role=role,
            model=model,
            key=key,
            cache_hit=cached,
            response_id=raw.get("id"),
            usage=usage,
            status=status,
        )
        if status != "completed":
            raise LLMError("Incomplete or failed model response; inspect saved response")
        if not schema:
            return raw
        if local:
            message = raw.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise LLMError("Ollama returned no assistant message")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                raise LLMError("Ollama returned no structured output")
            try:
                return schema.model_validate_json(content, strict=True)
            except ValueError as error:
                raise LLMError("Structured output failed local validation") from error
        texts = []
        for item in raw.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "refusal":
                        raise LLMError("Model refused this request; inspect saved response")
                    if content.get("type") == "output_text":
                        texts.append(content["text"])
        if not texts:
            raise LLMError("No structured output returned")
        try:
            return schema.model_validate_json("".join(texts))
        except ValueError as error:
            raise LLMError("Structured output failed local validation") from error

    def parse(
        self, role: str, model: str, system: str, payload: dict, schema: type[BaseModel]
    ) -> BaseModel:
        return self._call(
            role,
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": canonical(payload)},
            ],
            schema,
        )

    def search(self, query: str, domains: list[str]) -> list[dict]:
        response = self._call(
            "discovery",
            self.config.search_model,
            [
                {
                    "role": "user",
                    "content": "Find official municipal HTML pages or PDFs relevant to: " + query,
                }
            ],
            tools=[{"type": "web_search", "filters": {"allowed_domains": domains}}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
        )
        urls = {}
        for item in response.get("output", []):
            if item.get("type") == "web_search_call":
                for source in item.get("action", {}).get("sources", []):
                    if source.get("url"):
                        urls[source["url"]] = {
                            "url": source["url"],
                            "title": source.get("title", ""),
                        }
            elif item.get("type") == "message":
                for content in item.get("content", []):
                    for annotation in content.get("annotations", []):
                        if annotation.get("type") == "url_citation":
                            urls[annotation["url"]] = {
                                "url": annotation["url"],
                                "title": annotation.get("title", ""),
                            }
        # Only URLs from tool sources/citations; never URLs invented in answer prose.
        if not urls:
            raise LLMError("Web search returned no source URLs; inspect the saved response")
        return list(urls.values())


def check_ollama(config: LLM, *, client=None) -> None:
    """Check local connectivity/model availability without downloading or generating."""
    owned = client is None
    client = client or httpx.Client(timeout=5, trust_env=False)
    try:
        response = client.get(config.ollama_base_url + "/api/tags", timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        names = {item.get("name") for item in models if isinstance(item, dict)}
        if config.models[0] not in names:
            raise LLMError("Modellen saknas i Ollama. Hämta den med ollama pull och ange dess fullständiga namn, inklusive tagg.")
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        raise LLMError("Det gick inte att nå Ollama på din dator. Starta Ollama och kontrollera serveradressen.") from error
    finally:
        if owned:
            client.close()
