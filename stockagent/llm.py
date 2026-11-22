"""One structured-output LLM call, behind a provider switch (local Ollama or Claude).

Every agent node calls structured(), which returns a validated Pydantic object or raises LLMError.
Nodes never parse free text, so a malformed answer can't slip through as "evidence".
  - ollama   : /api/chat with the JSON schema as `format`, temperature 0, thinking off
  - anthropic: official Anthropic SDK, client.messages.parse(output_format=Schema)
"""
import json
import time
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _ollama(system: str, user: str, schema: type[T]) -> T:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_error = ""
    for attempt in range(1, 4):
        if last_error:   # tell the model what was wrong with its last output
            messages = messages[:2] + [{"role": "user", "content":
                       f"{user}\n\nYour previous reply was invalid ({last_error}). Reply with JSON matching the schema only."}]
        try:
            resp = requests.post(f"{config.OLLAMA_URL}/api/chat", timeout=1800, json={
                "model": config.OLLAMA_CHAT_MODEL,
                "messages": messages,
                "format": schema.model_json_schema(),
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_ctx": config.OLLAMA_NUM_CTX},
            })
            resp.raise_for_status()
            return schema.model_validate_json(resp.json()["message"]["content"])
        except ValidationError as exc:
            last_error = f"schema validation failed: {exc.errors()[:3]}"
        except (requests.RequestException, KeyError, json.JSONDecodeError) as exc:
            last_error = f"request failed: {exc}"
            time.sleep(3 * attempt)
    raise LLMError(f"Ollama call failed after 3 attempts: {last_error}")


def _anthropic(system: str, user: str, schema: type[T]) -> T:
    import anthropic   # imported lazily so the Ollama path doesn't need a key

    # Credentials: ANTHROPIC_API_KEY from .env / environment, or an `ant auth login` profile.
    client = anthropic.Anthropic(max_retries=4)   # SDK retries 408/409/429/5xx and connection errors
    for attempt in range(1, 3):
        try:
            response = client.messages.parse(
                model=config.ANTHROPIC_MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": "medium"},
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Claude API key missing or invalid: set ANTHROPIC_API_KEY in .env") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Claude rejected the request: {exc}") from exc
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as exc:
            if attempt == 2:
                raise LLMError(f"Claude API failed: {exc}") from exc
            time.sleep(5)
            continue
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined the request (stop_reason=refusal)")
        if response.parsed_output is not None:
            return response.parsed_output
    raise LLMError("Claude returned no valid structured output")


def structured(system: str, user: str, schema: type[T]) -> T:
    if config.LLM_PROVIDER == "anthropic":
        return _anthropic(system, user, schema)
    if config.LLM_PROVIDER == "ollama":
        return _ollama(system, user, schema)
    raise LLMError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' (use 'ollama' or 'anthropic')")


def describe() -> str:
    return (f"anthropic:{config.ANTHROPIC_MODEL}" if config.LLM_PROVIDER == "anthropic"
            else f"ollama:{config.OLLAMA_CHAT_MODEL}")
