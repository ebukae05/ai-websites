"""Tests for backend/leads.py. SMTP is always faked: no network."""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from backend import leads
from backend.leads import Lead, notify_owner, save_lead

SECRET = "unit-test-smtp-password-77"  # noqa: S105 (fake value for tests)
OWNER = "owner@acme.example"


def make_lead(**overrides: Any) -> Lead:
    values: dict[str, Any] = {
        "client": "acme-co",
        "name": "Jane Visitor",
        "phone": "555-0199",
        "email": "jane@visitor.example",
        "message": "Please call me about an order.",
    }
    values.update(overrides)
    return Lead(**values)


class FakeSMTP:
    instances: list[FakeSMTP] = []
    fail_on: str | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)
        self._maybe_fail("connect")

    def _maybe_fail(self, step: str) -> None:
        if FakeSMTP.fail_on == step:
            raise smtplib.SMTPException(f"{step} failed for {SECRET}")

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")
        self._maybe_fail("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.login_args = (user, password)
        self._maybe_fail("login")

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self._maybe_fail("send")
        self.sent.append(message)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "LEADS_DIR"):
        monkeypatch.delenv(name, raising=False)
    FakeSMTP.instances = []
    FakeSMTP.fail_on = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


def smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test.example")
    monkeypatch.setenv("SMTP_USER", "mailer@test.example")
    monkeypatch.setenv("SMTP_PASSWORD", SECRET)


# --- Lead ---------------------------------------------------------------------------------


def test_lead_created_at_is_utc_iso() -> None:
    lead = Lead(client="acme-co", name="A")
    assert lead.created_at.endswith("+00:00")
    assert lead.phone == lead.email == lead.message == ""


# --- save_lead ----------------------------------------------------------------------------


def test_save_lead_appends_json_lines(tmp_path: Path) -> None:
    first = make_lead()
    second = make_lead(name="Ünïcödé Visitor", phone="")
    path = save_lead(first, tmp_path / "leads")
    assert save_lead(second, tmp_path / "leads") == path
    assert path == tmp_path / "leads" / "acme-co.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {
        "client": "acme-co",
        "name": "Jane Visitor",
        "phone": "555-0199",
        "email": "jane@visitor.example",
        "message": "Please call me about an order.",
        "created_at": first.created_at,
    }
    assert json.loads(lines[1])["name"] == "Ünïcödé Visitor"


def test_newlines_in_fields_stay_on_one_line(tmp_path: Path) -> None:
    path = save_lead(make_lead(message="line one\nline two"), tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "line one\nline two"


def test_save_lead_uses_env_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEADS_DIR", str(tmp_path / "from-env"))
    path = save_lead(make_lead())
    assert path == tmp_path / "from-env" / "acme-co.jsonl"
    assert path.is_file()


def test_default_dir() -> None:
    assert leads.leads_dir_from_env() == Path("data/leads")


@pytest.mark.parametrize("slug", ["../escape", "a/b", "", "Acme"])
def test_save_lead_rejects_unsafe_slug(tmp_path: Path, slug: str) -> None:
    with pytest.raises(ValueError):
        save_lead(make_lead(client=slug), tmp_path)
    assert list(tmp_path.iterdir()) == []


# --- notify_owner -------------------------------------------------------------------------


def test_skipped_without_smtp_host(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    assert notify_owner(make_lead(), OWNER) is False
    assert FakeSMTP.instances == []
    assert "skipped" in caplog.text


def test_sends_with_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_FROM", "website@acme.example")
    lead = make_lead()
    assert notify_owner(lead, OWNER) is True
    (smtp,) = FakeSMTP.instances
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.test.example", 587, 10.0)
    assert smtp.calls == ["starttls", "login", "send", "quit"]
    assert smtp.login_args == ("mailer@test.example", SECRET)
    (message,) = smtp.sent
    assert message["To"] == OWNER
    assert message["From"] == "website@acme.example"
    assert message["Reply-To"] == lead.email
    body = message.get_content()
    for value in (lead.name, lead.phone, lead.email, lead.message):
        assert value in body


def test_custom_port_and_no_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.test.example")
    monkeypatch.setenv("SMTP_PORT", "2525")
    assert notify_owner(make_lead(), OWNER) is True
    (smtp,) = FakeSMTP.instances
    assert smtp.port == 2525
    assert "login" not in smtp.calls
    assert smtp.sent[0]["From"] == OWNER


def test_sender_falls_back_to_user(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_env(monkeypatch)
    notify_owner(make_lead(), OWNER)
    assert FakeSMTP.instances[0].sent[0]["From"] == "mailer@test.example"


@pytest.mark.parametrize("step", ["connect", "starttls", "login", "send"])
def test_smtp_failures_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, step: str
) -> None:
    smtp_env(monkeypatch)
    FakeSMTP.fail_on = step
    caplog.set_level(logging.DEBUG)
    lead = make_lead()
    assert notify_owner(lead, OWNER) is False
    assert "failed" in caplog.text
    for private in (SECRET, lead.name, lead.phone, lead.email, lead.message):
        assert private not in caplog.text


def test_network_error_logged_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_env(monkeypatch)

    def refuse(*args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    assert notify_owner(make_lead(), OWNER) is False


def test_bad_port_logged_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "not-a-number")
    assert notify_owner(make_lead(), OWNER) is False
    assert FakeSMTP.instances == []


def test_success_log_has_no_contact_details(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    smtp_env(monkeypatch)
    caplog.set_level(logging.DEBUG)
    lead = make_lead()
    notify_owner(lead, OWNER)
    for private in (SECRET, lead.name, lead.phone, lead.email, lead.message, OWNER):
        assert private not in caplog.text


def test_header_injection_in_lead_email_is_not_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp_env(monkeypatch)
    assert notify_owner(make_lead(email="x@y.example\nBcc: victim@z.example"), OWNER) is True
    message = FakeSMTP.instances[0].sent[0]
    assert message["Bcc"] is None
