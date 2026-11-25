"""Tests for backend/main.py. The model and email are always faked: no network."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import leads, llm, main

PACK: dict[str, Any] = {
    "bot_role": "You are the website assistant for a small business.",
    "prompt_rules": ["Keep answers short."],
    "default_faqs": [],
    "site": {"tagline": "{name}", "services_heading": "Services", "cta": "Ask"},
}
CLIENT: dict[str, Any] = {
    "name": "Acme Co",
    "industry": "test_pack",
    "phone": "555-0100",
    "email": "owner@acme.example",
    "hours": {"mon": "9:00-17:00"},
    "services": ["Consulting"],
}
USER_MSG = [{"role": "user", "content": "Are you open on Monday?"}]


class Recorder:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, list[dict[str, str]]]] = []
        self.notified: list[tuple[leads.Lead, str]] = []
        self.reply_text = "Yes, 9 to 5."
        self.error: Exception | None = None

    def reply(self, system: str, messages: list[dict[str, str]]) -> str:
        self.chat_calls.append((system, messages))
        if self.error is not None:
            raise self.error
        return self.reply_text

    def notify(self, lead: leads.Lead, owner_email: str) -> bool:
        self.notified.append((lead, owner_email))
        return True


@pytest.fixture
def rec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    clients = tmp_path / "clients"
    industries = tmp_path / "industries"
    (clients / "acme-co").mkdir(parents=True)
    industries.mkdir()
    (clients / "acme-co" / "config.yaml").write_text(yaml.safe_dump(CLIENT), encoding="utf-8")
    (industries / "test_pack.yaml").write_text(yaml.safe_dump(PACK), encoding="utf-8")
    monkeypatch.setattr(main, "CLIENTS_DIR", clients)
    monkeypatch.setattr(main, "INDUSTRIES_DIR", industries)
    monkeypatch.setenv("LEADS_DIR", str(tmp_path / "leads"))
    recorder = Recorder()
    monkeypatch.setattr(main, "llm_reply", recorder.reply)
    monkeypatch.setattr(leads, "notify_owner", recorder.notify)
    return recorder


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_backend_modules_import() -> None:
    for name in ("main", "llm", "prompt", "leads", "config_loader"):
        importlib.import_module(f"backend.{name}")


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- /chat --------------------------------------------------------------------------------


def test_chat_returns_reply(client: TestClient, rec: Recorder) -> None:
    response = client.post("/chat", json={"client": "acme-co", "messages": USER_MSG})
    assert response.status_code == 200
    assert response.json() == {"reply": "Yes, 9 to 5."}
    system, messages = rec.chat_calls[0]
    assert "Acme Co" in system
    assert "555-0100" in system
    assert messages == USER_MSG


def test_chat_passes_history(client: TestClient, rec: Recorder) -> None:
    history = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Open Monday?"},
    ]
    client.post("/chat", json={"client": "acme-co", "messages": history})
    assert rec.chat_calls[0][1] == history


def test_chat_unknown_client_hides_details(client: TestClient, rec: Recorder) -> None:
    for slug in ("nobody", "../etc", "ACME"):
        response = client.post("/chat", json={"client": slug, "messages": USER_MSG})
        assert response.status_code == 404
        assert response.json() == {"detail": "unknown client"}
    assert rec.chat_calls == []


def test_chat_broken_config_is_404_without_paths(
    client: TestClient, rec: Recorder, tmp_path: Path
) -> None:
    (tmp_path / "clients" / "acme-co" / "config.yaml").write_text("name: [", encoding="utf-8")
    response = client.post("/chat", json={"client": "acme-co", "messages": USER_MSG})
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown client"}
    assert str(tmp_path) not in response.text


def test_chat_llm_error_is_503_with_phone(client: TestClient, rec: Recorder) -> None:
    rec.error = llm.LLMError("AI model request failed: APITimeoutError")
    response = client.post("/chat", json={"client": "acme-co", "messages": USER_MSG})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "555-0100" in detail
    assert "APITimeoutError" not in detail


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"client": "acme-co"},
        {"client": "acme-co", "messages": []},
        {"client": "", "messages": USER_MSG},
        {"client": "acme-co", "messages": [{"role": "user", "content": ""}]},
        {"client": "acme-co", "messages": [{"role": "user", "content": "   "}]},
        {"client": "acme-co", "messages": [{"role": "user", "content": "x" * 2001}]},
        {"client": "acme-co", "messages": [{"role": "system", "content": "x"}]},
        {"client": "acme-co", "messages": [{"role": "user", "content": 5}]},
        {"client": "acme-co", "messages": ["hello"]},
        {
            "client": "acme-co",
            "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        },
        {"client": "acme-co", "messages": [{"role": "user", "content": "a"}] * 21},
    ],
)
def test_chat_rejects_bad_input(client: TestClient, rec: Recorder, body: Any) -> None:
    response = client.post("/chat", json=body)
    assert response.status_code == 422
    assert rec.chat_calls == []


def test_chat_accepts_limits(client: TestClient, rec: Recorder) -> None:
    messages = [{"role": "user", "content": "x" * 2000}] * 20
    response = client.post("/chat", json={"client": "acme-co", "messages": messages})
    assert response.status_code == 200


def test_chat_with_real_sample_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_reply(system: str, messages: list[dict[str, str]]) -> str:
        seen.append(system)
        return "ok"

    monkeypatch.setattr(main, "llm_reply", fake_reply)
    response = client.post("/chat", json={"client": "riverside-plumbing", "messages": USER_MSG})
    assert response.status_code == 200
    assert "555-0142" in seen[0]


# --- /lead --------------------------------------------------------------------------------


def read_leads(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "leads" / "acme-co.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_lead_saved_and_owner_notified(client: TestClient, rec: Recorder, tmp_path: Path) -> None:
    body = {"client": "acme-co", "name": " Jane ", "phone": "555-0199", "message": "Call me"}
    response = client.post("/lead", json=body)
    assert response.status_code == 201
    assert response.json() == {"ok": True}
    (saved,) = read_leads(tmp_path)
    assert saved["client"] == "acme-co"
    assert saved["name"] == "Jane"
    assert saved["phone"] == "555-0199"
    assert saved["email"] == ""
    assert saved["message"] == "Call me"
    (lead, owner) = rec.notified[0]
    assert owner == "owner@acme.example"
    assert lead.name == "Jane"


def test_lead_with_email_only(client: TestClient, rec: Recorder, tmp_path: Path) -> None:
    body = {"client": "acme-co", "name": "Jane", "email": "jane@visitor.example"}
    assert client.post("/lead", json=body).status_code == 201
    assert read_leads(tmp_path)[0]["email"] == "jane@visitor.example"


@pytest.mark.parametrize(
    "body",
    [
        {"client": "acme-co", "phone": "1"},
        {"client": "acme-co", "name": "", "phone": "1"},
        {"client": "acme-co", "name": "   ", "phone": "1"},
        {"client": "acme-co", "name": "x" * 101, "phone": "1"},
        {"client": "acme-co", "name": "Jane"},
        {"client": "acme-co", "name": "Jane", "phone": "  ", "email": ""},
        {"client": "acme-co", "name": "Jane", "phone": "1", "message": "x" * 2001},
        {"name": "Jane", "phone": "1"},
    ],
)
def test_lead_rejects_bad_input(
    client: TestClient, rec: Recorder, tmp_path: Path, body: Any
) -> None:
    assert client.post("/lead", json=body).status_code == 422
    assert not (tmp_path / "leads").exists()
    assert rec.notified == []


def test_lead_unknown_client(client: TestClient, rec: Recorder, tmp_path: Path) -> None:
    response = client.post("/lead", json={"client": "nobody", "name": "Jane", "phone": "1"})
    assert response.status_code == 404
    assert response.json() == {"detail": "unknown client"}
    assert not (tmp_path / "leads").exists()


def test_lead_save_failure_is_503(
    client: TestClient, rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(lead: leads.Lead) -> Path:
        raise PermissionError("read-only disk")

    monkeypatch.setattr(leads, "save_lead", broken)
    response = client.post("/lead", json={"client": "acme-co", "name": "Jane", "phone": "1"})
    assert response.status_code == 503
    assert "555-0100" in response.json()["detail"]
    assert rec.notified == []


def test_lead_email_failure_still_201(
    client: TestClient, rec: Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(leads, "notify_owner", lambda lead, owner: False)
    response = client.post("/lead", json={"client": "acme-co", "name": "Jane", "phone": "1"})
    assert response.status_code == 201
    assert len(read_leads(tmp_path)) == 1


# --- CORS ---------------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["null", "http://localhost:5500"])
def test_cors_preflight_allowed(client: TestClient, origin: str) -> None:
    response = client.options(
        "/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] in ("*", origin)


def test_cors_header_on_simple_request(client: TestClient) -> None:
    response = client.get("/health", headers={"Origin": "null"})
    assert response.headers["access-control-allow-origin"] in ("*", "null")


def test_allowed_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    assert main._allowed_origins() == ["*"]
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.example , null,, ")
    assert main._allowed_origins() == ["https://a.example", "null"]
