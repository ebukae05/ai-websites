"""Tests for build.py. Everything is written under tmp_path."""

from __future__ import annotations

import re
import subprocess  # noqa: S404 - runs this repo's own build.py
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml

import build
from backend.config_loader import FAQ, ClientConfig, IndustryPack, SiteCopy, load_config

ROOT = Path(__file__).resolve().parent.parent

PACK: dict[str, Any] = {
    "bot_role": "You are the website assistant for a small business.",
    "default_faqs": [{"question": "Do you offer gift cards?", "answer": "Yes, in store."}],
    "site": {"tagline": "{name}: here to help.", "services_heading": "What we do", "cta": "Ask"},
}
CLIENT: dict[str, Any] = {
    "name": "Acme Co",
    "industry": "test_pack",
    "phone": "+1 555-0100",
    "email": "owner@acme.example",
    "hours": {"mon": "9:00-17:00", "sun": "closed"},
    "services": ["Consulting", "Widgets & gadgets"],
    "faqs": [{"question": "Where are you?", "answer": "Main Street."}],
}


def make_config(**overrides: Any) -> ClientConfig:
    values: dict[str, Any] = {
        "slug": "acme-co",
        "name": "Acme Co",
        "industry": "test_pack",
        "phone": "555-0100",
        "email": "owner@acme.example",
        "hours": MappingProxyType({"mon": "9:00-17:00"}),
        "services": ("Consulting",),
        "faqs": (FAQ("Where are you?", "Main Street."),),
        "pack": IndustryPack(
            industry="test_pack",
            bot_role="role",
            prompt_rules=(),
            default_faqs=(),
            site=SiteCopy(
                tagline="{name}: here to help.", services_heading="What we do", cta="Ask"
            ),
        ),
    }
    values.update(overrides)
    return ClientConfig(**values)


def write_dirs(tmp_path: Path, client: dict[str, Any] = CLIENT) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    industries = tmp_path / "industries"
    (clients / "acme-co").mkdir(parents=True)
    industries.mkdir()
    (clients / "acme-co" / "config.yaml").write_text(yaml.safe_dump(client), encoding="utf-8")
    (industries / "test_pack.yaml").write_text(yaml.safe_dump(PACK), encoding="utf-8")
    return clients, industries


class Collector(HTMLParser):
    """Collects script tags, attributes and text so tests can check what the browser sees."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.body_attrs: dict[str, str | None] = {}
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))
        if tag == "body":
            self.body_attrs = dict(attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parse(page: str) -> Collector:
    collector = Collector()
    collector.feed(page)
    return collector


# --- render -------------------------------------------------------------------------------


def test_script_in_name_stays_inert() -> None:
    evil = '<script>alert(1)</script>" onload="x'
    page = build.render(make_config(name=evil, services=(evil,), faqs=(FAQ(evil, evil),)))
    assert "<script>alert" not in page
    parsed = parse(page)
    assert parsed.scripts == [{"src": "chat.js"}]
    assert parsed.body_attrs["data-name"] == evil
    assert "onload" not in parsed.body_attrs
    assert evil in "".join(parsed.text)


def test_no_leftover_tokens() -> None:
    page = build.render(make_config())
    assert "{{" not in page
    assert "}}" not in page


def test_value_that_looks_like_a_token_is_kept() -> None:
    page = build.render(make_config(name="{{slug}} Co"))
    assert "{{slug}} Co" in page


def test_unfilled_template_token_fails() -> None:
    with pytest.raises(build.BuildError, match="mystery"):
        build.render(make_config(), template="<p>{{name}} {{mystery}}</p>")


def test_all_facts_present() -> None:
    config = make_config(
        hours=MappingProxyType({"mon": "9:00-17:00", "sun": "closed"}),
        services=("Consulting", "Widgets"),
    )
    page = build.render(config, api_url="https://api.acme.example/", year=2030)
    parsed = parse(page)
    text = " ".join(parsed.text)
    for fact in ("Acme Co", "555-0100", "Consulting", "Widgets", "Where are you?", "Main Street."):
        assert fact in text
    assert "Acme Co: here to help." in text
    assert "What we do" in text
    assert "Ask" in text
    assert "2030" in text
    assert 'href="tel:5550100"' in page
    assert parsed.body_attrs == {
        "data-client": "acme-co",
        "data-api": "https://api.acme.example",
        "data-phone": "555-0100",
        "data-name": "Acme Co",
    }
    rows = re.findall(r'<th scope="row">(\w+)</th><td>([^<]*)</td>', page)
    assert [r[0] for r in rows] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert dict(rows)["Monday"] == "9:00-17:00"
    assert dict(rows)["Sunday"] == "closed"
    assert dict(rows)["Tuesday"] == "Not listed"
    assert "<details><summary>Where are you?</summary><p>Main Street.</p></details>" in page


def test_faq_section_hidden_when_empty() -> None:
    page = build.render(make_config(faqs=()))
    assert re.search(r'<section[^>]*id="faq"[^>]*hidden', page)


def test_phone_href_keeps_plus() -> None:
    assert build.phone_href("+1 (555) 010-0") == "+15550100"
    assert build.phone_href("555-0100") == "5550100"


@pytest.mark.parametrize(
    ("phone", "href"),
    [
        ("555-0100 ext 12", "5550100"),
        ("555-0100 Ext. 12", "5550100"),
        ("+44 20 7946 0000 x204", "+442079460000"),
        ("555-0100, 555-0199", "5550100"),
        ("555-0100; after hours 555-0199", "5550100"),
    ],
)
def test_phone_href_uses_main_number_only(phone: str, href: str) -> None:
    assert build.phone_href(phone) == href


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://api.acme.example/", "https://api.acme.example"),
        ("Http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_api_url_scheme_case_insensitive(url: str, expected: str) -> None:
    assert build.check_api_url(url) == expected


@pytest.mark.parametrize(
    "url", ["http://localhost:8000/?x=1", "http://localhost:8000#frag", "https://a.example/p?"]
)
def test_api_url_rejects_query_and_fragment(url: str) -> None:
    with pytest.raises(build.BuildError, match="query"):
        build.check_api_url(url)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x", "", 'http://a" b'])
def test_bad_api_url_rejected(url: str) -> None:
    with pytest.raises(build.BuildError, match="--api"):
        build.render(make_config(), api_url=url)


# --- build --------------------------------------------------------------------------------


def test_build_writes_site(tmp_path: Path) -> None:
    clients, industries = write_dirs(tmp_path)
    out = tmp_path / "dist"
    target = build.build(
        "acme-co", "http://localhost:9000", out, clients_dir=clients, industries_dir=industries
    )
    assert target == out / "acme-co"
    assert sorted(p.name for p in target.iterdir()) == ["chat.js", "index.html", "styles.css"]
    page = (target / "index.html").read_text(encoding="utf-8")
    assert "Widgets &amp; gadgets" in page
    assert 'data-api="http://localhost:9000"' in page
    assert "Do you offer gift cards?" in page  # merged pack default
    assert (target / "chat.js").read_bytes() == (ROOT / "site" / "chat.js").read_bytes()


def test_build_recreates_previous_build(tmp_path: Path) -> None:
    clients, industries = write_dirs(tmp_path)
    out = tmp_path / "dist"
    target = build.build("acme-co", None, out, clients_dir=clients, industries_dir=industries)
    (target / "stale.txt").write_text("old", encoding="utf-8")
    build.build("acme-co", None, out, clients_dir=clients, industries_dir=industries)
    assert not (target / "stale.txt").exists()


def test_build_into_empty_existing_folder(tmp_path: Path) -> None:
    clients, industries = write_dirs(tmp_path)
    out = tmp_path / "dist"
    (out / "acme-co").mkdir(parents=True)
    target = build.build("acme-co", None, out, clients_dir=clients, industries_dir=industries)
    assert (target / "index.html").is_file()


@pytest.mark.parametrize(
    "existing",
    [
        {"notes.txt": "keep me"},
        {"index.html": "x", "styles.css": "x", "chat.js": "x", "config.yaml": "name: x"},
        {"index.html": "x", "styles.css": "x", "chat.js": "x", "sub/tool.py": "x = 1"},
    ],
)
def test_build_refuses_folder_that_is_not_a_build(tmp_path: Path, existing: dict[str, str]) -> None:
    clients, industries = write_dirs(tmp_path)
    target = tmp_path / "dist" / "acme-co"
    for name, text in existing.items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(text, encoding="utf-8")
    with pytest.raises(build.BuildError, match="not a previous build"):
        build.build("acme-co", None, tmp_path / "dist", clients, industries)
    for name, text in existing.items():
        assert (target / name).read_text(encoding="utf-8") == text


def test_main_into_clients_dir_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients, industries = write_dirs(tmp_path)
    with pytest.raises(build.BuildError):
        build.build("acme-co", None, clients, clients, industries)
    assert (clients / "acme-co" / "config.yaml").is_file()


def test_api_url_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients, industries = write_dirs(tmp_path)
    monkeypatch.setenv("API_URL", "https://chat.acme.example")
    target = build.build("acme-co", None, tmp_path / "d", clients, industries)
    assert 'data-api="https://chat.acme.example"' in (target / "index.html").read_text("utf-8")


def test_api_url_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients, industries = write_dirs(tmp_path)
    monkeypatch.delenv("API_URL", raising=False)
    target = build.build("acme-co", None, tmp_path / "d", clients, industries)
    assert 'data-api="http://localhost:8000"' in (target / "index.html").read_text("utf-8")


# --- CLI ----------------------------------------------------------------------------------


def test_main_unknown_slug_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert build.main(["no-such-client", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "Build failed" in err
    assert "Traceback" not in err


def test_main_bad_slug_exits_1(tmp_path: Path) -> None:
    assert build.main(["../escape", "--out", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []


def test_cli_subprocess_unknown_slug(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed arguments, our own script
        [sys.executable, str(ROOT / "build.py"), "no-such-client", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Build failed" in result.stderr


def test_real_sample_builds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert build.main(["riverside-plumbing", "--out", str(tmp_path)]) == 0
    page = (tmp_path / "riverside-plumbing" / "index.html").read_text(encoding="utf-8")
    config = load_config("riverside-plumbing")
    assert "{{" not in page
    assert build.esc(config.name) in page
    for service in config.services:
        assert build.esc(service) in page
    for faq in config.faqs:
        assert build.esc(faq.question) in page
    assert (tmp_path / "riverside-plumbing" / "styles.css").is_file()
