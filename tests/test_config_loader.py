"""Tests for backend/config_loader.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.config_loader import (
    FAQ,
    ClientConfig,
    ConfigError,
    load_config,
    load_industry_pack,
    merge_faqs,
)

PACK: dict[str, Any] = {
    "bot_role": "You are the website assistant for a small business.",
    "prompt_rules": ["Keep answers short."],
    "default_faqs": [
        {"question": "Do you offer gift cards?", "answer": "Ask us in person."},
        {"question": "WHERE ARE YOU?", "answer": "Pack default location answer."},
    ],
    "site": {"tagline": "{name}: here to help.", "services_heading": "Services", "cta": "Ask"},
}


def client_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Acme Co",
        "industry": "test_pack",
        "phone": "555-0100",
        "email": "owner@acme.example",
        "hours": {"tue": "9:00-17:00", "mon": "9:00-17:00", "sun": "closed"},
        "services": ["Consulting", "Widgets"],
        "faqs": [{"question": "Where are you?", "answer": "Main Street."}],
    }
    data.update(overrides)
    return data


def write_files(
    tmp_path: Path,
    data: Any,
    pack: Any = PACK,
    slug: str = "acme-co",
) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    industries = tmp_path / "industries"
    (clients / slug).mkdir(parents=True)
    industries.mkdir()
    (clients / slug / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    if pack is not None:
        (industries / "test_pack.yaml").write_text(yaml.safe_dump(pack), encoding="utf-8")
    return clients, industries


def load(tmp_path: Path, data: Any, pack: Any = PACK) -> ClientConfig:
    clients, industries = write_files(tmp_path, data, pack)
    return load_config("acme-co", clients_dir=clients, industries_dir=industries)


def test_loads_valid_config(tmp_path: Path) -> None:
    config = load(tmp_path, client_data())
    assert config.slug == "acme-co"
    assert config.name == "Acme Co"
    assert config.industry == "test_pack"
    assert config.phone == "555-0100"
    assert config.services == ("Consulting", "Widgets")
    assert list(config.hours) == ["mon", "tue", "sun"]
    assert config.hours["sun"] == "closed"
    assert config.pack.bot_role.startswith("You are")
    assert config.pack.prompt_rules == ("Keep answers short.",)
    assert config.pack.site.cta == "Ask"


def test_config_is_frozen(tmp_path: Path) -> None:
    config = load(tmp_path, client_data())
    with pytest.raises(AttributeError):
        config.name = "Other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.hours["mon"] = "never"  # type: ignore[index]


def test_faqs_merge_client_first_and_skip_duplicates(tmp_path: Path) -> None:
    config = load(tmp_path, client_data())
    assert [f.question for f in config.faqs] == ["Where are you?", "Do you offer gift cards?"]
    assert config.faqs[0].answer == "Main Street."


def test_faqs_optional(tmp_path: Path) -> None:
    data = client_data()
    del data["faqs"]
    config = load(tmp_path, data)
    assert len(config.faqs) == 2


def test_merge_faqs_directly() -> None:
    a = (FAQ("Q1", "client"),)
    b = (FAQ(" q1 ", "default"), FAQ("Q2", "default"))
    assert merge_faqs(a, b) == (FAQ("Q1", "client"), FAQ("Q2", "default"))


@pytest.mark.parametrize("field", ["name", "industry", "phone", "email", "hours", "services"])
def test_missing_required_field(tmp_path: Path, field: str) -> None:
    data = client_data()
    del data[field]
    with pytest.raises(ConfigError, match=f"'{field}' is missing"):
        load(tmp_path, data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "   "), ("phone", ""), ("hours", {}), ("services", [])],
)
def test_blank_required_field(tmp_path: Path, field: str, value: Any) -> None:
    with pytest.raises(ConfigError, match=f"'{field}' is blank"):
        load(tmp_path, client_data(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", 42, "'name' must be a string"),
        ("hours", ["mon"], "'hours' must be a mapping"),
        ("hours", {"funday": "9-5"}, "unknown day 'funday'"),
        ("hours", {"mon": 900}, "'hours.mon' must be"),
        ("services", "Widgets", "'services' must be a list"),
        ("services", ["ok", 3], "'services' item 2"),
        ("faqs", "nope", "'faqs' must be a list"),
        ("email", "not-an-email", "'email' must be an email address"),
    ],
)
def test_wrong_types(tmp_path: Path, field: str, value: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, client_data(**{field: value}))


@pytest.mark.parametrize(
    "faq",
    [
        "just a string",
        {"question": "Q only"},
        {"question": "Q", "answer": "A", "extra": "x"},
        {"question": "", "answer": "A"},
        {"question": "Q", "answer": 5},
    ],
)
def test_bad_faq_shape(tmp_path: Path, faq: Any) -> None:
    with pytest.raises(ConfigError, match="faqs"):
        load(tmp_path, client_data(faqs=[faq]))


def test_unknown_industry(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown industry 'no_such_pack'"):
        load(tmp_path, client_data(industry="no_such_pack"))


def test_missing_pack_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no pack file"):
        load(tmp_path, client_data(), pack=None)


def test_industry_with_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown industry"):
        load(tmp_path, client_data(industry="../secrets"))


def test_bad_pack_shape(tmp_path: Path) -> None:
    pack = dict(PACK)
    del pack["site"]
    with pytest.raises(ConfigError, match="'site' is required"):
        load(tmp_path, client_data(), pack=pack)


def test_pack_missing_bot_role(tmp_path: Path) -> None:
    pack = dict(PACK)
    del pack["bot_role"]
    with pytest.raises(ConfigError, match="'bot_role' is missing"):
        load(tmp_path, client_data(), pack=pack)


@pytest.mark.parametrize(
    "slug", ["../x", "..", "a/b", "a\\b", "Acme", "acme_co", "", "-acme", "acme--co", "a b"]
)
def test_invalid_slug(tmp_path: Path, slug: str) -> None:
    with pytest.raises(ConfigError, match="Invalid client slug"):
        load_config(slug, clients_dir=tmp_path, industries_dir=tmp_path)


def test_missing_client(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No config file"):
        load_config("nobody", clients_dir=tmp_path, industries_dir=tmp_path)


def test_invalid_yaml(tmp_path: Path) -> None:
    clients, industries = write_files(tmp_path, client_data())
    (clients / "acme-co" / "config.yaml").write_text("name: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config("acme-co", clients_dir=clients, industries_dir=industries)


def test_top_level_not_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mapping of fields"):
        load(tmp_path, ["a", "list"])


def test_load_industry_pack_directly(tmp_path: Path) -> None:
    _, industries = write_files(tmp_path, client_data())
    pack = load_industry_pack("test_pack", industries_dir=industries)
    assert pack.industry == "test_pack"
    assert pack.site.tagline == "{name}: here to help."


def test_loads_real_sample_client() -> None:
    config = load_config("riverside-plumbing")
    assert config.industry == "home_services"
    assert config.pack.industry == "home_services"
    assert set(config.hours) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert 5 <= len(config.services) <= 7
    assert len(config.faqs) >= 4
