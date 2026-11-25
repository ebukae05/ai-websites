"""Reads and checks each client's info file and the industry pack it names.

`load_config(slug)` returns a frozen `ClientConfig`. Anything wrong with the files raises a
single `ConfigError` whose message says which file and which field is the problem.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_CLIENTS_DIR = BACKEND_DIR.parent / "clients"
DEFAULT_INDUSTRIES_DIR = BACKEND_DIR / "industries"

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INDUSTRY_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class ConfigError(Exception):
    """A client config or industry pack is missing or invalid."""


@dataclass(frozen=True)
class FAQ:
    question: str
    answer: str


@dataclass(frozen=True)
class SiteCopy:
    tagline: str
    services_heading: str
    cta: str


@dataclass(frozen=True)
class IndustryPack:
    industry: str
    bot_role: str
    prompt_rules: tuple[str, ...]
    default_faqs: tuple[FAQ, ...]
    site: SiteCopy


@dataclass(frozen=True)
class ClientConfig:
    slug: str
    name: str
    industry: str
    phone: str
    email: str
    hours: Mapping[str, str]  # read-only; keys are in DAYS order
    services: tuple[str, ...]
    faqs: tuple[FAQ, ...]  # client FAQs first, then pack defaults not already covered
    pack: IndustryPack


def load_config(
    slug: str,
    clients_dir: Path | None = None,
    industries_dir: Path | None = None,
) -> ClientConfig:
    """Load and validate `clients/<slug>/config.yaml` plus its industry pack."""
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ConfigError(
            f"Invalid client slug {slug!r}: use lowercase letters, digits and single hyphens."
        )
    base = clients_dir if clients_dir is not None else DEFAULT_CLIENTS_DIR
    path = base / slug / "config.yaml"
    if not path.is_file():
        raise ConfigError(f"No config file for client {slug!r} (expected {path}).")
    data = _read_yaml_mapping(path)
    where = f"{path}"

    name = _required_str(data, "name", where, single_line=True)
    industry = _required_str(data, "industry", where)
    phone = _required_str(data, "phone", where, single_line=True)
    email = _required_str(data, "email", where, single_line=True)
    if not _looks_like_email(email):
        raise ConfigError(f"{where}: field 'email' must be an email address, got {email!r}.")
    hours = _parse_hours(data, where)
    services = _parse_services(data, where)
    client_faqs = _parse_faqs(data.get("faqs"), "faqs", where, required=False)

    pack = load_industry_pack(industry, industries_dir)
    return ClientConfig(
        slug=slug,
        name=name,
        industry=industry,
        phone=phone,
        email=email,
        hours=hours,
        services=services,
        faqs=merge_faqs(client_faqs, pack.default_faqs),
        pack=pack,
    )


def load_industry_pack(industry: str, industries_dir: Path | None = None) -> IndustryPack:
    """Load and validate `backend/industries/<industry>.yaml`."""
    if not isinstance(industry, str) or not _INDUSTRY_RE.fullmatch(industry):
        raise ConfigError(
            f"Unknown industry {industry!r}: use lowercase letters, digits and underscores."
        )
    base = industries_dir if industries_dir is not None else DEFAULT_INDUSTRIES_DIR
    path = base / f"{industry}.yaml"
    if not path.is_file():
        raise ConfigError(f"Unknown industry {industry!r}: no pack file at {path}.")
    data = _read_yaml_mapping(path)
    where = f"{path}"

    bot_role = _required_str(data, "bot_role", where)
    rules_raw = data.get("prompt_rules")
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise ConfigError(f"{where}: field 'prompt_rules' must be a list of strings.")
    prompt_rules = tuple(
        _list_item_str(item, "prompt_rules", i, where) for i, item in enumerate(rules_raw)
    )
    default_faqs = _parse_faqs(data.get("default_faqs"), "default_faqs", where, required=False)

    site_raw = data.get("site")
    if not isinstance(site_raw, dict):
        raise ConfigError(f"{where}: field 'site' is required and must be a mapping.")
    site_where = f"{where} (site)"
    site = SiteCopy(
        tagline=_required_str(site_raw, "tagline", site_where),
        services_heading=_required_str(site_raw, "services_heading", site_where),
        cta=_required_str(site_raw, "cta", site_where),
    )
    return IndustryPack(
        industry=industry,
        bot_role=bot_role,
        prompt_rules=prompt_rules,
        default_faqs=default_faqs,
        site=site,
    )


def merge_faqs(client_faqs: tuple[FAQ, ...], default_faqs: tuple[FAQ, ...]) -> tuple[FAQ, ...]:
    """Client FAQs first, then defaults whose question is not already present (any case)."""
    seen = {faq.question.strip().casefold() for faq in client_faqs}
    merged = list(client_faqs)
    for faq in default_faqs:
        key = faq.question.strip().casefold()
        if key not in seen:
            seen.add(key)
            merged.append(faq)
    return tuple(merged)


# --- helpers -------------------------------------------------------------------------------


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping of fields at the top level.")
    return data


def _has_line_break_or_control(text: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)


def _looks_like_email(email: str) -> bool:
    """One '@', a non-empty local part, and a domain with a dot that is not at either end."""
    if email.count("@") != 1 or any(ch.isspace() for ch in email):
        return False
    local, domain = email.split("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def _required_str(
    data: Mapping[str, Any], field: str, where: str, *, single_line: bool = False
) -> str:
    if field not in data or data[field] is None:
        raise ConfigError(f"{where}: required field '{field}' is missing.")
    value = data[field]
    if not isinstance(value, str):
        raise ConfigError(f"{where}: field '{field}' must be a string, got {type(value).__name__}.")
    if not value.strip():
        raise ConfigError(f"{where}: required field '{field}' is blank.")
    value = value.strip()
    if single_line and _has_line_break_or_control(value):
        raise ConfigError(
            f"{where}: field '{field}' must be a single line "
            "(no line breaks or control characters)."
        )
    return value


def _list_item_str(
    item: object, field: str, index: int, where: str, *, single_line: bool = False
) -> str:
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{where}: '{field}' item {index + 1} must be a non-blank string.")
    value = item.strip()
    if single_line and _has_line_break_or_control(value):
        raise ConfigError(
            f"{where}: '{field}' item {index + 1} must be a single line "
            "(no line breaks or control characters)."
        )
    return value


def _parse_hours(data: Mapping[str, Any], where: str) -> Mapping[str, str]:
    raw = data.get("hours")
    if raw is None:
        raise ConfigError(f"{where}: required field 'hours' is missing.")
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: field 'hours' must be a mapping of day -> hours.")
    if not raw:
        raise ConfigError(f"{where}: required field 'hours' is blank.")
    hours: dict[str, str] = {}
    for day, value in raw.items():
        if day not in DAYS:
            raise ConfigError(
                f"{where}: 'hours' has unknown day {day!r}; use one of {', '.join(DAYS)}."
            )
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"{where}: 'hours.{day}' must be a non-blank string like \"9:00-17:00\" "
                'or "closed".'
            )
        if _has_line_break_or_control(value.strip()):
            raise ConfigError(
                f"{where}: 'hours.{day}' must be a single line "
                "(no line breaks or control characters)."
            )
        hours[day] = value.strip()
    ordered = {day: hours[day] for day in DAYS if day in hours}
    return MappingProxyType(ordered)


def _parse_services(data: Mapping[str, Any], where: str) -> tuple[str, ...]:
    raw = data.get("services")
    if raw is None:
        raise ConfigError(f"{where}: required field 'services' is missing.")
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: field 'services' must be a list of strings.")
    if not raw:
        raise ConfigError(f"{where}: required field 'services' is blank.")
    return tuple(
        _list_item_str(item, "services", i, where, single_line=True) for i, item in enumerate(raw)
    )


def _parse_faqs(raw: object, field: str, where: str, *, required: bool) -> tuple[FAQ, ...]:
    if raw is None:
        if required:
            raise ConfigError(f"{where}: required field '{field}' is missing.")
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{where}: field '{field}' must be a list of question/answer pairs.")
    faqs: list[FAQ] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"question", "answer"}:
            raise ConfigError(
                f"{where}: '{field}' item {i + 1} must have exactly the keys "
                "'question' and 'answer'."
            )
        item_where = f"{where} ({field} item {i + 1})"
        faqs.append(
            FAQ(
                question=_required_str(item, "question", item_where),
                answer=_required_str(item, "answer", item_where),
            )
        )
    return tuple(faqs)
