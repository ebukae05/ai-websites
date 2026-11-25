"""Saves leads and emails them to the owner.

`save_lead` appends one JSON line per lead to `<LEADS_DIR>/<client>.jsonl`.
`notify_owner` emails the owner over SMTP (STARTTLS). Email problems are logged, never
raised: by the time the email is sent the lead is already saved.

Logs name the client slug and the kind of failure only, never contact details or secrets.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from backend.config_loader import is_valid_slug

logger = logging.getLogger(__name__)

DEFAULT_LEADS_DIR = "data/leads"
DEFAULT_SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 10.0

_write_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Lead:
    client: str  # client slug
    name: str
    phone: str = ""
    email: str = ""
    message: str = ""
    created_at: str = field(default_factory=_now_iso)  # UTC, ISO 8601


def leads_dir_from_env() -> Path:
    return Path(os.environ.get("LEADS_DIR", "").strip() or DEFAULT_LEADS_DIR)


def save_lead(lead: Lead, leads_dir: Path | None = None) -> Path:
    """Append `lead` as one JSON line to `<leads_dir>/<client>.jsonl` and return that path.

    `leads_dir` defaults to env LEADS_DIR, else "data/leads". Raises ValueError for an
    unsafe client slug and OSError if the file cannot be written.
    """
    if not is_valid_slug(lead.client):
        raise ValueError("Invalid client slug for lead.")
    directory = leads_dir if leads_dir is not None else leads_dir_from_env()
    path = directory / f"{lead.client}.jsonl"
    line = json.dumps(asdict(lead), ensure_ascii=False) + "\n"
    with _write_lock:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    return path


def build_email(lead: Lead, owner_email: str, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "New enquiry from your website"
    message["From"] = sender
    message["To"] = owner_email
    if lead.email:
        try:
            message["Reply-To"] = lead.email
        except ValueError:
            pass  # not a usable header value; the address is still in the body
    body = [
        "Someone left their details on your website.",
        "",
        f"Name:    {lead.name}",
        f"Phone:   {lead.phone or '-'}",
        f"Email:   {lead.email or '-'}",
        f"Time:    {lead.created_at} (UTC)",
        "",
        "Message:",
        lead.message or "-",
    ]
    message.set_content("\n".join(body))
    return message


def notify_owner(lead: Lead, owner_email: str) -> bool:
    """Email the lead to the owner. Returns True if sent; logs and returns False otherwise."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        logger.info("SMTP_HOST is not set; skipped lead email for client %s", lead.client)
        return False
    port_text = os.environ.get("SMTP_PORT", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user or owner_email
    try:
        port = int(port_text) if port_text else DEFAULT_SMTP_PORT
        message = build_email(lead, owner_email, sender)
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if user:
                smtp.login(user, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        logger.warning("Lead email failed for client %s: %s", lead.client, type(exc).__name__)
        return False
    logger.info("Lead email sent for client %s", lead.client)
    return True
