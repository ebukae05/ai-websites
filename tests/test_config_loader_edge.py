"""Tester probes for backend/config_loader.py: odd inputs, wrong types, edge cases.

Spec: DESIGN.md 3.1 (client config) and 3.2 (industry packs).
"""

from __future__ import annotations

import time
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
        {"question": "Do you offer gift cards?", "answer": "Pack answer: gift cards."},
        {"question": "Is there parking?", "answer": "Pack answer: parking."},
    ],
    "site": {"tagline": "{name}: here to help.", "services_heading": "Services", "cta": "Ask"},
}


def client_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Acme Co",
        "industry": "test_pack",
        "phone": "555-0100",
        "email": "owner@acme.example",
        "hours": {"mon": "9:00-17:00", "sun": "closed"},
        "services": ["Consulting", "Widgets"],
    }
    data.update(overrides)
    return data


def setup_dirs(tmp_path: Path, slug: str = "acme-co") -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    industries = tmp_path / "industries"
    (clients / slug).mkdir(parents=True)
    industries.mkdir()
    (industries / "test_pack.yaml").write_text(yaml.safe_dump(PACK), encoding="utf-8")
    return clients, industries


def load_text(tmp_path: Path, text: str, *, encoding: str = "utf-8") -> ClientConfig:
    clients, industries = setup_dirs(tmp_path)
    (clients / "acme-co" / "config.yaml").write_bytes(text.encode(encoding))
    return load_config("acme-co", clients_dir=clients, industries_dir=industries)


def load_data(tmp_path: Path, data: Any) -> ClientConfig:
    return load_text(tmp_path, yaml.safe_dump(data, allow_unicode=True))


def load_pack_data(tmp_path: Path, pack: Any) -> ClientConfig:
    clients, industries = setup_dirs(tmp_path)
    (clients / "acme-co" / "config.yaml").write_text(
        yaml.safe_dump(client_data()), encoding="utf-8"
    )
    (industries / "test_pack.yaml").write_text(yaml.safe_dump(pack), encoding="utf-8")
    return load_config("acme-co", clients_dir=clients, industries_dir=industries)


# --- whole-file problems ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "   \n\n", "# only a comment\n", "just a string\n", "42\n", "- a\n- b\n", "null\n"],
)
def test_file_that_is_not_a_mapping(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError, match="mapping of fields"):
        load_text(tmp_path, text)


def test_non_utf8_file_is_config_error(tmp_path: Path) -> None:
    text = yaml.safe_dump(client_data(name="Café"), allow_unicode=True)
    with pytest.raises(ConfigError, match="Could not read"):
        load_text(tmp_path, text, encoding="utf-16")


def test_utf8_bom_file_loads(tmp_path: Path) -> None:
    # Windows editors often save with a BOM; the file must still load.
    text = yaml.safe_dump(client_data(), allow_unicode=True)
    config = load_text(tmp_path, text, encoding="utf-8-sig")
    assert config.name == "Acme Co"


def test_alias_bomb_fails_fast(tmp_path: Path) -> None:
    lines = ['a0: &a0 ["x", "x", "x", "x", "x", "x", "x", "x", "x"]']
    for i in range(1, 10):
        prev = f"*a{i - 1}"
        lines.append(f"a{i}: &a{i} [{', '.join([prev] * 9)}]")
    data = yaml.safe_dump(client_data()) + "\n".join(lines) + "\nservices: *a9\n"
    # `services` appears twice; the bomb version (last) wins in PyYAML.
    start = time.monotonic()
    with pytest.raises(ConfigError, match="'services' item 1"):
        load_text(tmp_path, data)
    assert time.monotonic() - start < 5


# --- wrong types per field ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", None, "'name' is missing"),
        ("name", ["Acme"], "'name' must be a string"),
        ("name", {"a": 1}, "'name' must be a string"),
        ("name", True, "'name' must be a string"),
        ("phone", 5550142, "'phone' must be a string"),
        ("phone", 555.0142, "'phone' must be a string"),
        ("phone", None, "'phone' is missing"),
        ("email", None, "'email' is missing"),
        ("email", 7, "'email' must be a string"),
        ("industry", None, "'industry' is missing"),
        ("industry", 3, "'industry' must be a string"),
        ("industry", True, "'industry' must be a string"),
        ("hours", None, "'hours' is missing"),
        ("hours", "mon-fri 9-5", "'hours' must be a mapping"),
        ("hours", ["mon: 9-5", "tue: 9-5"], "'hours' must be a mapping"),
        ("hours", 9, "'hours' must be a mapping"),
        ("hours", {"mon": None}, "'hours.mon' must be"),
        ("hours", {"mon": "   "}, "'hours.mon' must be"),
        ("hours", {"mon": ["9", "5"]}, "'hours.mon' must be"),
        ("hours", {"mon": False}, "'hours.mon' must be"),
        ("hours", {"Mon": "9-5"}, "unknown day 'Mon'"),
        ("hours", {"monday": "9-5"}, "unknown day 'monday'"),
        ("hours", {1: "9-5"}, "unknown day 1"),
        ("services", None, "'services' is missing"),
        ("services", "Consulting, Widgets", "'services' must be a list"),
        ("services", {"a": "b"}, "'services' must be a list"),
        ("services", [None], "'services' item 1"),
        ("services", ["ok", "  "], "'services' item 2"),
        ("services", [["nested"]], "'services' item 1"),
        ("services", [42], "'services' item 1"),
        ("faqs", {"question": "Q", "answer": "A"}, "'faqs' must be a list"),
        ("faqs", "Q? A.", "'faqs' must be a list"),
        ("faqs", [{"question": "Q"}], "faqs"),
        ("faqs", [{"answer": "A"}], "faqs"),
        ("faqs", [{"question": "Q", "answer": None}], "'answer' is missing"),
        ("faqs", [{"question": None, "answer": "A"}], "'question' is missing"),
        ("faqs", [{"question": 1, "answer": "A"}], "'question' must be a string"),
        ("faqs", [{"question": "Q", "answer": 1}], "'answer' must be a string"),
        ("faqs", [{"question": "Q", "answer": "   "}], "'answer' is blank"),
        ("faqs", [None], "faqs"),
        ("faqs", [["Q", "A"]], "faqs"),
    ],
)
def test_wrong_types_are_config_errors(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_data(tmp_path, client_data(**{field: value}))


def test_raw_yaml_off_value_in_hours_is_rejected(tmp_path: Path) -> None:
    # YAML 1.1 reads an unquoted `off` as False; the loader must not crash on it.
    text = yaml.safe_dump(client_data(hours={"mon": "9-5"})) + "\n"
    text = text.replace("mon: 9-5", "mon: 9-5\n  sat: off")
    with pytest.raises(ConfigError, match="'hours.sat' must be"):
        load_text(tmp_path, text)


def test_faqs_null_means_no_client_faqs(tmp_path: Path) -> None:
    config = load_data(tmp_path, client_data(faqs=None))
    assert [f.question for f in config.faqs] == ["Do you offer gift cards?", "Is there parking?"]


def test_values_are_stripped(tmp_path: Path) -> None:
    config = load_data(
        tmp_path,
        client_data(name="  Acme Co  ", phone=" 555-0100 ", services=["  Consulting  "]),
    )
    assert config.name == "Acme Co"
    assert config.phone == "555-0100"
    assert config.services == ("Consulting",)


def test_error_messages_name_the_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_data(tmp_path, client_data(services="oops"))
    assert "config.yaml" in str(info.value)


@pytest.mark.parametrize("email", ["@", "owner@", "@example.com", "owner@@example.com"])
def test_invalid_email_rejected(tmp_path: Path, email: str) -> None:
    with pytest.raises(ConfigError, match="'email'"):
        load_data(tmp_path, client_data(email=email))


# --- unicode and size ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Café Müller & Söhne", "東京 配管サービス", "Ünïcödé 🚀 Co", 'O\'Brien "Best" Co']
)
def test_unicode_business_names(tmp_path: Path, name: str) -> None:
    config = load_data(tmp_path, client_data(name=name))
    assert config.name == name


def test_very_large_config_loads(tmp_path: Path) -> None:
    services = [f"Service number {i}" for i in range(5000)]
    faqs = [{"question": f"Question {i}?", "answer": "A" * 2000} for i in range(500)]
    start = time.monotonic()
    config = load_data(tmp_path, client_data(services=services, faqs=faqs, name="N" * 10_000))
    assert time.monotonic() - start < 10
    assert len(config.services) == 5000
    assert len(config.faqs) == 502


# --- slugs and industries -----------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "../clients/acme-co",
        "..\\acme-co",
        "acme-co/../acme-co",
        "/etc/passwd",
        "C:\\Windows",
        "acme-co\n",
        "acme-co\x00",
        "acme-co ",
        " acme-co",
        "acme-co.",
        "café",
        "acme-co-",
        "ACME-CO",
        "%2e%2e",
        "~acme",
    ],
)
def test_odd_slugs_rejected(tmp_path: Path, slug: str) -> None:
    clients, industries = setup_dirs(tmp_path)
    with pytest.raises(ConfigError, match="Invalid client slug"):
        load_config(slug, clients_dir=clients, industries_dir=industries)


@pytest.mark.parametrize("slug", [None, 5, b"acme-co", ["acme-co"]])
def test_non_string_slug_rejected(tmp_path: Path, slug: Any) -> None:
    with pytest.raises(ConfigError, match="Invalid client slug"):
        load_config(slug, clients_dir=tmp_path, industries_dir=tmp_path)


@pytest.mark.parametrize("slug", ["a" * 300, "a" * 5000, "con", "nul", "aux", "com1"])
def test_long_or_reserved_slugs_give_config_error(tmp_path: Path, slug: str) -> None:
    clients, industries = setup_dirs(tmp_path)
    with pytest.raises(ConfigError, match="No config file"):
        load_config(slug, clients_dir=clients, industries_dir=industries)


@pytest.mark.parametrize(
    "industry",
    [
        "Home_Services",
        "test-pack",
        "test_pack.yaml",
        "../industries/test_pack",
        "_test_pack",
        "test__pack",
        "test pack",
        "",
        "   ",
        "a" * 5000,
        "nul",
    ],
)
def test_unknown_or_odd_industry_is_load_error(tmp_path: Path, industry: str) -> None:
    with pytest.raises(ConfigError, match="industry"):
        load_data(tmp_path, client_data(industry=industry))


def test_industry_pack_file_must_be_exact(tmp_path: Path) -> None:
    # A directory with the pack's name is not a pack file.
    clients, industries = setup_dirs(tmp_path)
    (industries / "dir_pack.yaml").mkdir()
    with pytest.raises(ConfigError, match="no pack file"):
        load_industry_pack("dir_pack", industries_dir=industries)


@pytest.mark.parametrize("industry", [None, 5, ["test_pack"]])
def test_load_industry_pack_non_string(tmp_path: Path, industry: Any) -> None:
    with pytest.raises(ConfigError, match="Unknown industry"):
        load_industry_pack(industry, industries_dir=tmp_path)


# --- industry pack shape ------------------------------------------------------------------


def pack_with(**overrides: Any) -> dict[str, Any]:
    pack = dict(PACK)
    pack.update(overrides)
    return pack


@pytest.mark.parametrize(
    ("pack", "message"),
    [
        (pack_with(prompt_rules="Keep answers short."), "'prompt_rules' must be a list"),
        (pack_with(prompt_rules=[None]), "'prompt_rules' item 1"),
        (pack_with(prompt_rules=[5]), "'prompt_rules' item 1"),
        (pack_with(bot_role=["a"]), "'bot_role' must be a string"),
        (pack_with(bot_role=""), "'bot_role' is blank"),
        (pack_with(default_faqs={"question": "Q", "answer": "A"}), "'default_faqs' must be"),
        (pack_with(default_faqs=[{"question": "Q"}]), "default_faqs"),
        (pack_with(site="tagline"), "'site' is required"),
        (pack_with(site=None), "'site' is required"),
        (pack_with(site={"tagline": "t", "services_heading": "s"}), "'cta' is missing"),
        (pack_with(site={"tagline": 1, "services_heading": "s", "cta": "c"}), "'tagline'"),
    ],
)
def test_bad_pack_values(tmp_path: Path, pack: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_pack_data(tmp_path, pack)


@pytest.mark.parametrize("pack", [[], "text", None, 5])
def test_pack_not_a_mapping(tmp_path: Path, pack: Any) -> None:
    with pytest.raises(ConfigError, match="mapping of fields"):
        load_pack_data(tmp_path, pack)


def test_empty_pack_file(tmp_path: Path) -> None:
    clients, industries = setup_dirs(tmp_path)
    (industries / "empty_pack.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping of fields"):
        load_industry_pack("empty_pack", industries_dir=industries)


def test_pack_optional_lists_may_be_absent(tmp_path: Path) -> None:
    pack = pack_with()
    del pack["prompt_rules"]
    del pack["default_faqs"]
    config = load_pack_data(tmp_path, pack)
    assert config.pack.prompt_rules == ()
    assert config.faqs == ()


# --- FAQ merging ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    ["do you offer gift cards?", "DO YOU OFFER GIFT CARDS?", "  Do you offer gift cards?  "],
)
def test_client_faq_overrides_pack_default_any_case(tmp_path: Path, question: str) -> None:
    faqs = [{"question": question, "answer": "Client answer."}]
    config = load_data(tmp_path, client_data(faqs=faqs))
    gift = [f for f in config.faqs if "gift" in f.question.lower()]
    assert gift == [FAQ(question.strip(), "Client answer.")]
    assert config.faqs[0].answer == "Client answer."


def test_pack_default_used_when_client_has_none_for_question(tmp_path: Path) -> None:
    faqs = [{"question": "Do you offer gift cards?", "answer": "Client answer."}]
    config = load_data(tmp_path, client_data(faqs=faqs))
    assert config.faqs[-1] == FAQ("Is there parking?", "Pack answer: parking.")


def test_casefold_handles_unicode_case() -> None:
    client = (FAQ("STRASSE?", "client"),)
    default = (FAQ("straße?", "default"),)
    assert merge_faqs(client, default) == client


def test_duplicate_pack_defaults_collapse() -> None:
    default = (FAQ("Q?", "first"), FAQ("q?", "second"))
    assert merge_faqs((), default) == (FAQ("Q?", "first"),)


# --- the real sample client and pack ------------------------------------------------------


def test_real_pack_loads_with_all_fields() -> None:
    pack = load_industry_pack("home_services")
    assert pack.bot_role
    assert len(pack.prompt_rules) >= 1
    assert len(pack.default_faqs) >= 1
    assert "{name}" in pack.site.tagline
    assert pack.site.services_heading
    assert pack.site.cta


def test_real_client_matches_design_fields() -> None:
    config = load_config("riverside-plumbing")
    assert config.slug == "riverside-plumbing"
    assert config.name
    assert config.phone
    assert "@" in config.email
    assert list(config.hours) == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert len(set(config.services)) == len(config.services)
    questions = [f.question.casefold() for f in config.faqs]
    assert len(set(questions)) == len(questions)
    # Client FAQs come before pack defaults.
    pack_qs = {f.question.casefold() for f in config.pack.default_faqs}
    first_pack = next(i for i, q in enumerate(questions) if q in pack_qs)
    assert all(q in pack_qs for q in questions[first_pack:])
