"""Tester probes for backend/llm.py.

These drive the real SDK client end to end, but swap its HTTP transport for an in-memory
fake (httpx2.MockTransport), so no request ever leaves the process.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from backend import llm

ROOT = Path(__file__).resolve().parent.parent
FAKE_KEY = "unit-test-credential-value-0042"
MESSAGES = [{"role": "user", "content": "Hello"}]

Handler = Callable[[httpx2.Request], httpx2.Response]


def message_json(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class Server:
    """Records every request and answers with `handler`."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def use_server(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], Server]:
    real_anthropic = anthropic.Anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    def install(handler: Handler) -> Server:
        server = Server(handler)

        def factory(**kwargs: Any) -> anthropic.Anthropic:
            transport = httpx2.MockTransport(server)
            return real_anthropic(http_client=httpx2.Client(transport=transport), **kwargs)

        monkeypatch.setattr(llm, "_client", None)
        monkeypatch.setattr(anthropic, "Anthropic", factory)
        return server

    return install


def ok(text: str) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json([{"type": "text", "text": text}]))

    return handler


def error(status: int) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = {"type": "error", "error": {"type": "x", "message": f"bad key {FAKE_KEY}"}}
        return httpx2.Response(status, headers={"retry-after-ms": "1"}, json=body)

    return handler


# --- happy path through the real SDK ------------------------------------------------------


def test_real_sdk_round_trip(use_server: Callable[[Handler], Server]) -> None:
    server = use_server(ok("Hi there!"))
    assert llm.reply("You are a bot.", MESSAGES) == "Hi there!"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.url.path.endswith("/v1/messages")
    body = request.read().decode()
    assert '"system":"You are a bot."' in body.replace(": ", ":")
    assert f'"max_tokens":{llm.MAX_TOKENS}' in body.replace(": ", ":")


def test_reply_is_stripped(use_server: Callable[[Handler], Server]) -> None:
    use_server(ok("\n\n  Hello.  \n"))
    assert llm.reply("s", MESSAGES) == "Hello."


@pytest.mark.parametrize(
    "content",
    [[], [{"type": "text", "text": ""}], [{"type": "text", "text": " \n\t "}]],
)
def test_empty_response_is_llm_error(
    use_server: Callable[[Handler], Server], content: list[dict[str, Any]]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json(content))

    use_server(handler)
    with pytest.raises(llm.LLMError, match="empty"):
        llm.reply("s", MESSAGES)


# --- failures from the API ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "name"),
    [
        (400, "BadRequestError"),
        (401, "AuthenticationError"),
        (403, "PermissionDeniedError"),
        (404, "NotFoundError"),
        (429, "RateLimitError"),
        (500, "InternalServerError"),
        (529, "OverloadedError"),
    ],
)
def test_api_errors_wrapped_without_secret(
    use_server: Callable[[Handler], Server], status: int, name: str
) -> None:
    use_server(error(status))
    with pytest.raises(llm.LLMError) as info:
        llm.reply("s", MESSAGES)
    assert name in str(info.value)
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(info.value)


def test_server_errors_retried_once(use_server: Callable[[Handler], Server]) -> None:
    server = use_server(error(500))
    with pytest.raises(llm.LLMError):
        llm.reply("s", MESSAGES)
    assert len(server.requests) == 1 + llm.MAX_RETRIES


def test_auth_error_not_retried(use_server: Callable[[Handler], Server]) -> None:
    server = use_server(error(401))
    with pytest.raises(llm.LLMError):
        llm.reply("s", MESSAGES)
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "exc_type", [httpx2.ReadTimeout, httpx2.ConnectTimeout, httpx2.ConnectError]
)
def test_network_failures_wrapped(
    use_server: Callable[[Handler], Server], exc_type: type[Exception]
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("simulated network failure", request=request)  # type: ignore[call-arg]

    use_server(handler)
    with pytest.raises(llm.LLMError) as info:
        llm.reply("s", MESSAGES)
    assert FAKE_KEY not in str(info.value)


def test_timeout_is_configured_on_request(use_server: Callable[[Handler], Server]) -> None:
    server = use_server(ok("ok"))
    llm.reply("s", MESSAGES)
    assert server.requests[0].headers["x-stainless-read-timeout"] == str(llm.TIMEOUT_SECONDS)


def test_non_json_success_body_is_llm_error(use_server: Callable[[Handler], Server]) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=b"<html>gateway</html>", headers={"content-type": "text/html"}
        )

    use_server(handler)
    with pytest.raises(llm.LLMError):
        llm.reply("s", MESSAGES)


# --- missing key ----------------------------------------------------------------------------


def test_missing_key_is_llm_error_and_sends_nothing(
    use_server: Callable[[Handler], Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    server = use_server(ok("should not be reached"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="credentials"):
        llm.reply("s", MESSAGES)
    assert server.requests == []


def test_key_never_in_error_even_if_sdk_message_has_it(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def create(self, **kwargs: Any) -> Any:
            raise anthropic.AnthropicError(f"failed with key {FAKE_KEY}")

    class FakeClient:
        messages = Boom()

    monkeypatch.setattr(llm, "_client", FakeClient())
    with pytest.raises(llm.LLMError) as info:
        llm.reply("s", MESSAGES)
    assert FAKE_KEY not in str(info.value)


# --- message validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "system", "content": "x"}],
        [{"role": "User", "content": "x"}],
        [{"role": "tool", "content": "x"}],
        [{"role": "", "content": "x"}],
        [{"content": "x"}],
        [{"role": "user", "content": None}],
        [{"role": "user", "content": 5}],
        [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
        [{"role": "user", "content": "ok"}, {"role": "admin", "content": "x"}],
    ],
)
def test_bad_messages_rejected_before_any_request(
    use_server: Callable[[Handler], Server], messages: Any
) -> None:
    server = use_server(ok("nope"))
    with pytest.raises(llm.LLMError):
        llm.reply("s", messages)
    assert server.requests == []


def test_extra_message_keys_not_forwarded(use_server: Callable[[Handler], Server]) -> None:
    server = use_server(ok("ok"))
    llm.reply("s", [{"role": "user", "content": "hi", "cache_control": "x", "name": "evil"}])
    body = server.requests[0].read().decode()
    assert "evil" not in body
    assert "cache_control" not in body


@pytest.mark.parametrize("messages", [["hello"], [None], [42]])
def test_non_dict_message_is_llm_error(
    use_server: Callable[[Handler], Server], messages: Any
) -> None:
    use_server(ok("nope"))
    with pytest.raises(llm.LLMError):
        llm.reply("s", messages)


# --- isolation rule -------------------------------------------------------------------------


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_only_llm_module_imports_the_sdk() -> None:
    """CLAUDE.md: backend/llm.py is the only file that calls the AI model."""
    files = [*sorted((ROOT / "backend").rglob("*.py")), ROOT / "build.py"]
    assert ROOT / "backend" / "llm.py" in files
    offenders = [
        str(path.relative_to(ROOT))
        for path in files
        if path.name != "llm.py" and "anthropic" in imports_of(path)
    ]
    assert offenders == []
    # Dynamic imports such as importlib.import_module("anthropic") count too.
    dynamic_offenders = [
        str(path.relative_to(ROOT))
        for path in files
        if path.name != "llm.py"
        and any(q in path.read_text(encoding="utf-8") for q in ('"anthropic"', "'anthropic'"))
    ]
    assert dynamic_offenders == []
