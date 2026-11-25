"""Tests for backend/llm.py. The SDK client is always faked: no network calls."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from backend import llm

# The SDK only stores the request on the error, so any object will do here.
FAKE_REQUEST: Any = SimpleNamespace(method="POST", url="https://api.example")

MESSAGES = [{"role": "user", "content": "Hello"}]


class FakeMessages:
    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def text_response(*texts: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=t) for t in texts])


def install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeMessages) -> None:
    monkeypatch.setattr(llm, "_client", SimpleNamespace(messages=fake))


def test_reply_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessages(text_response("Hi ", "there"))
    install_fake(monkeypatch, fake)
    assert llm.reply("system text", MESSAGES) == "Hi there"
    call = fake.calls[0]
    assert call["system"] == "system text"
    assert call["messages"] == MESSAGES
    assert call["max_tokens"] == llm.MAX_TOKENS


def test_model_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessages(text_response("ok"))
    install_fake(monkeypatch, fake)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    llm.reply("s", MESSAGES)
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-other-model")
    llm.reply("s", MESSAGES)
    assert [c["model"] for c in fake.calls] == ["claude-haiku-5-5", "some-other-model"]


def test_blank_model_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessages(text_response("ok"))
    install_fake(monkeypatch, fake)
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    llm.reply("s", MESSAGES)
    assert fake.calls[0]["model"] == llm.DEFAULT_MODEL


@pytest.mark.parametrize("response", [text_response(), text_response("   ")])
def test_empty_reply_raises(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    install_fake(monkeypatch, FakeMessages(response))
    with pytest.raises(llm.LLMError, match="empty"):
        llm.reply("s", MESSAGES)


def test_non_text_blocks_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="Yes")]
    )
    install_fake(monkeypatch, FakeMessages(response))
    assert llm.reply("s", MESSAGES) == "Yes"


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APITimeoutError(request=FAKE_REQUEST),
        anthropic.APIConnectionError(request=FAKE_REQUEST),
        anthropic.AnthropicError("boom"),
        TypeError("Could not resolve authentication method"),
    ],
)
def test_sdk_errors_wrapped(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    install_fake(monkeypatch, FakeMessages(error=error))
    with pytest.raises(llm.LLMError):
        llm.reply("s", MESSAGES)


@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "system", "content": "x"}], [{"role": "user"}]],
)
def test_bad_messages_rejected(monkeypatch: pytest.MonkeyPatch, messages: Any) -> None:
    fake = FakeMessages(text_response("ok"))
    install_fake(monkeypatch, fake)
    with pytest.raises(llm.LLMError):
        llm.reply("s", messages)
    assert fake.calls == []


def test_client_created_lazily_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    def fake_anthropic(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(messages=FakeMessages(text_response("ok")))

    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(anthropic, "Anthropic", fake_anthropic)
    assert created == []
    llm.reply("s", MESSAGES)
    llm.reply("s", MESSAGES)
    assert len(created) == 1
    assert created[0]["timeout"] == llm.TIMEOUT_SECONDS
    assert "api_key" not in created[0]
