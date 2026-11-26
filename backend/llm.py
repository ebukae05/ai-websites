"""The only file that talks to the AI model.

`reply(system, messages)` sends one chat turn and returns the model's text. The API key is
read by the SDK from the ANTHROPIC_API_KEY environment variable; the model name comes from
ANTHROPIC_MODEL. Any failure is raised as `LLMError` so callers handle one exception type.
"""

from __future__ import annotations

import os

import anthropic
from anthropic.types import MessageParam

DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_SECONDS = 20.0
MAX_TOKENS = 400
MAX_RETRIES = 1

_client: anthropic.Anthropic | None = None


class LLMError(Exception):
    """The model could not produce a usable reply."""


def _get_client() -> anthropic.Anthropic:
    """Create the SDK client on first use so importing this module needs no API key."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
    return _client


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL


def _to_message_params(messages: list[dict[str, str]]) -> list[MessageParam]:
    params: list[MessageParam] = []
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            raise LLMError(f"Message {i + 1} must be a mapping with role and content.")
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            raise LLMError(f"Message {i + 1} must have role user/assistant and text content.")
        if role == "user":
            params.append({"role": "user", "content": content})
        else:
            params.append({"role": "assistant", "content": content})
    if not params:
        raise LLMError("At least one message is required.")
    return params


def reply(system: str, messages: list[dict[str, str]]) -> str:
    """Return the model's text reply to `messages` under the `system` instructions."""
    params = _to_message_params(messages)
    try:
        response = _get_client().messages.create(
            model=_model(),
            max_tokens=MAX_TOKENS,
            system=system,
            messages=params,
        )
    except anthropic.AnthropicError as exc:
        raise LLMError(f"AI model request failed: {type(exc).__name__}") from exc
    except TypeError as exc:
        # The SDK raises TypeError("Could not resolve authentication method...") when no
        # API key can be found; any other TypeError gets a generic label.
        if "authentication" in str(exc).lower():
            raise LLMError("AI model request failed: missing credentials") from exc
        raise LLMError("AI model request failed: TypeError") from exc

    try:
        text = "".join(block.text for block in response.content if block.type == "text")
    except (AttributeError, TypeError) as exc:
        # e.g. a 200 response whose body was not JSON, so the SDK returned raw text.
        raise LLMError("AI model returned a response in an unexpected format.") from exc
    text = text.strip()
    if not text:
        raise LLMError("AI model returned an empty reply.")
    return text
