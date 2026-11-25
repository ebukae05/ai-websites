"""Tester probes for backend/leads.py. SMTP is always faked: no network."""

from __future__ import annotations

import itertools
import json
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from backend.leads import Lead, build_email, notify_owner, save_lead

SECRET = "unit-test-smtp-password-53"  # noqa: S105 (fake value for tests)
OWNER = "owner@acme.example"


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        pass

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.test.example")
    monkeypatch.setenv("SMTP_USER", "mailer@test.example")
    monkeypatch.setenv("SMTP_PASSWORD", SECRET)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)


def test_concurrent_save_lead_threads_valid_jsonl(tmp_path: Path) -> None:
    def worker(n: int) -> None:
        for i in range(25):
            save_lead(Lead(client="acme-co", name=f"{n}-{i}", message="ü" * 2000), tmp_path)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (tmp_path / "acme-co.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 16 * 25
    names = {json.loads(line)["name"] for line in lines}
    assert len(names) == 16 * 25


@pytest.mark.parametrize(
    "email",
    [
        "x@y.example\r\nBcc: victim@z.example",
        "x@y.example\nTo: victim@z.example",
        "x@y.example\r\n\r\nInjected body",
    ],
)
def test_build_email_header_injection(email: str) -> None:
    message = build_email(Lead(client="acme-co", name="n", email=email), OWNER, "s@x.example")
    head = message.as_bytes().replace(b"\r\n", b"\n").split(b"\n\n", 1)[0]
    assert b"victim@z.example" not in head
    assert b"Injected body" not in head
    assert message.get_all("To") == [OWNER]


@pytest.mark.parametrize("email", ['"', ',"', "():", ':;"'])
def test_notify_owner_never_raises_on_odd_email(
    email: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lead = Lead(client="acme-co", name="Jane", phone="555-0199", email=email)
    assert notify_owner(lead, OWNER) in (True, False)
    assert SECRET not in caplog.text


def test_fix_ea3a095_fuzz_short_addresses_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms bug 6 fix: every 1-3 char mix of address punctuation is handled."""
    monkeypatch.setattr(ssl, "create_default_context", lambda: None)  # speed only
    chars = '"\\()<>@,;:[]. a'
    candidates = ["".join(c) for n in (1, 2, 3) for c in itertools.product(chars, repeat=n)]
    for email in candidates:
        lead = Lead(client="acme-co", name="Jane", phone="1", email=email)
        message = build_email(lead, OWNER, "s@x.example")
        message.as_bytes()  # serialising must not raise either
        assert notify_owner(lead, OWNER) is True, repr(email)
    assert len(FakeSMTP.sent) == len(candidates)
