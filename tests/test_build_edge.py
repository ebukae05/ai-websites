"""Tester probes for build.py and the built page (site/template.html, styles.css, chat.js).

Everything is written under tmp_path; nothing touches the repo's dist/.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - runs this repo's own build.py
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml

import build
from backend.config_loader import FAQ, ClientConfig, ConfigError, IndustryPack, SiteCopy

ROOT = Path(__file__).resolve().parent.parent

PACK: dict[str, Any] = {
    "bot_role": "You are the website assistant for a small business.",
    "default_faqs": [],
    "site": {"tagline": "{name}: here to help.", "services_heading": "What we do", "cta": "Ask"},
}
CLIENT: dict[str, Any] = {
    "name": "Acme Co",
    "industry": "test_pack",
    "phone": "555-0100",
    "email": "owner@acme.example",
    "hours": {"mon": "9:00-17:00"},
    "services": ["Consulting"],
}


def make_config(
    *,
    name: str = "Acme Co",
    phone: str = "555-0100",
    services: tuple[str, ...] = ("Consulting",),
    faqs: tuple[FAQ, ...] = (FAQ("Where are you?", "Main Street."),),
    hours: dict[str, str] | None = None,
    tagline: str = "{name}: here to help.",
    cta: str = "Ask",
    heading: str = "What we do",
) -> ClientConfig:
    return ClientConfig(
        slug="acme-co",
        name=name,
        industry="test_pack",
        phone=phone,
        email="owner@acme.example",
        hours=MappingProxyType(hours if hours is not None else {"mon": "9:00-17:00"}),
        services=services,
        faqs=faqs,
        pack=IndustryPack(
            industry="test_pack",
            bot_role="role",
            prompt_rules=(),
            default_faqs=(),
            site=SiteCopy(tagline=tagline, services_heading=heading, cta=cta),
        ),
    )


class Page(HTMLParser):
    """Collects tags, attributes and text of a rendered page."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.stack: list[str] = []
        self.texts_in: dict[str, list[str]] = {}
        self.feed(text)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.stack:
            self.texts_in.setdefault(self.stack[-1], []).append(data)

    def first(self, tag: str) -> dict[str, str | None]:
        return next(attrs for t, attrs in self.tags if t == tag)

    def all(self, tag: str) -> list[dict[str, str | None]]:
        return [attrs for t, attrs in self.tags if t == tag]

    def joined(self, tag: str) -> str:
        return "".join(self.texts_in.get(tag, []))


NASTY = [
    '"><script>alert(1)</script>',
    "' onmouseover='alert(1)",
    "</script><script>alert(1)</script>",
    "{{slug}} {{api_url}} {{ name }}",
    "&amp; &lt;b&gt; &#x3c;",
    "Ünïcödé 東京 🚀 مرحبا",
    "back\\slash \\1 \\g<0> $1",
]


# --- escaping -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", NASTY)
def test_name_round_trips_in_attributes_and_text(name: str) -> None:
    page_html = build.render(make_config(name=name), year=2026)
    page = Page(page_html)
    body = page.first("body")
    assert body["data-name"] == name
    assert body["data-client"] == "acme-co"
    assert body["data-api"] == "http://localhost:8000"
    assert page.joined("title") == name
    assert page.joined("h1") == name
    assert len(page.all("script")) == 1
    assert "<script>alert" not in page_html


@pytest.mark.parametrize("phone", ['555"0100', "555<0100>", "{{name}}", "+44 (0)20 7946 0958"])
def test_phone_escaped_everywhere(phone: str) -> None:
    page_html = build.render(make_config(phone=phone), year=2026)
    page = Page(page_html)
    assert page.first("body")["data-phone"] == phone
    for attrs in page.all("a"):
        href = attrs.get("href") or ""
        if href.startswith("tel:"):
            assert set(href[4:]) <= set("+0123456789")


@pytest.mark.parametrize("text", NASTY)
def test_faq_service_hours_tagline_cta_escaped(text: str) -> None:
    config = make_config(
        services=(text,),
        faqs=(FAQ(text, text),),
        hours={"mon": text},
        tagline=f"{{name}} {text}",
        cta=text,
        heading=text,
    )
    page_html = build.render(config, year=2026)
    page = Page(page_html)
    assert len(page.all("script")) == 1
    assert page.joined("li") == text
    assert page.joined("summary") == text
    assert page.joined("td").startswith(text)
    assert page.joined("h2").startswith(text)
    assert page.first("body")["data-client"] == "acme-co"


def test_script_close_in_faq_is_text() -> None:
    answer = "Call us </script><script src=https://evil.example/x.js></script>"
    page_html = build.render(make_config(faqs=(FAQ("Q?", answer),)), year=2026)
    page = Page(page_html)
    assert [a.get("src") for a in page.all("script")] == ["chat.js"]
    assert page.joined("p").count("evil.example") == 1  # present only as visible text
    assert "</script><script" not in page_html


def test_tokens_in_values_are_not_expanded() -> None:
    page_html = build.render(make_config(name="{{phone}}", phone="555-0100"), year=2026)
    page = Page(page_html)
    assert page.joined("h1") == "{{phone}}"
    assert page.first("body")["data-name"] == "{{phone}}"


# --- content edge cases -------------------------------------------------------------------


def test_empty_faq_list_hides_section() -> None:
    page = Page(build.render(make_config(faqs=()), year=2026))
    faq = next(attrs for t, attrs in page.tags if t == "section" and attrs.get("id") == "faq")
    assert "hidden" in faq
    assert page.all("details") == []


def test_faqs_present_section_visible() -> None:
    page = Page(build.render(make_config(), year=2026))
    faq = next(attrs for t, attrs in page.tags if t == "section" and attrs.get("id") == "faq")
    assert "hidden" not in faq
    assert len(page.all("details")) == 1


def test_missing_hours_days_shown_as_not_listed() -> None:
    page = Page(build.render(make_config(hours={"wed": "10-12"}), year=2026))
    rows = page.joined("th")
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
        assert day in rows
    assert page.joined("td").count("Not listed") == 6
    assert "10-12" in page.joined("td")


def test_many_services_and_faqs() -> None:
    services = tuple(f"Service {i}" for i in range(300))
    faqs = tuple(FAQ(f"Q{i}?", f"A{i}") for i in range(200))
    page = Page(build.render(make_config(services=services, faqs=faqs), year=2026))
    assert len(page.all("li")) == 300
    assert len(page.all("details")) == 200


# --- --api validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "api",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "file:///etc/passwd",
        "ftp://example.com",
        "//evil.example",
        'http://a.example" onload="alert(1)',
        "http://a.example'x",
        "http://a.example<b>",
        "http://a b.example",
        "http://a.example\nb",
        "http://a.example\tb",
        "http://",
        "http:///",
        "",
        "   ",
        "localhost:8000",
    ],
)
def test_bad_api_rejected(api: str) -> None:
    with pytest.raises(build.BuildError, match="--api"):
        build.render(make_config(), api_url=api, year=2026)


@pytest.mark.parametrize(
    ("api", "expected"),
    [
        ("  https://api.example.com/  ", "https://api.example.com"),
        ("http://localhost:8000///", "http://localhost:8000"),
        ("https://api.example.com/v1", "https://api.example.com/v1"),
        ("https://api.example.com/a=1&b=2", "https://api.example.com/a=1&b=2"),
    ],
)
def test_good_api_normalised_and_escaped(api: str, expected: str) -> None:
    page_html = build.render(make_config(), api_url=api, year=2026)
    assert Page(page_html).first("body")["data-api"] == expected
    if "&" in expected:
        assert "&amp;b=2" in page_html


# --- references in the built page ---------------------------------------------------------


def test_page_references_only_local_assets_and_tel() -> None:
    page_html = build.render(make_config(), api_url="https://api.example.com", year=2026)
    page = Page(page_html)
    refs: list[str] = []
    for _, attrs in page.tags:
        for key in ("href", "src", "action", "poster", "srcset", "formaction"):
            value = attrs.get(key)
            if value is not None:
                refs.append(value)
    assert refs
    for ref in refs:
        assert ref in ("styles.css", "chat.js", "#main") or ref.startswith("tel:"), ref
    assert [a.get("href") for a in page.all("link")] == ["styles.css"]
    assert [a.get("src") for a in page.all("script")] == ["chat.js"]
    # The only absolute URL anywhere in the page is the backend URL in data-api.
    without_api = page_html.replace('data-api="https://api.example.com"', "")
    assert "http://" not in without_api
    assert "https://" not in without_api


def test_styles_have_no_external_references() -> None:
    css = (ROOT / "site" / "styles.css").read_text(encoding="utf-8").lower()
    assert "@import" not in css
    assert "http:" not in css
    assert "https:" not in css
    assert "url(" not in css or all(
        part.split(")")[0].strip(" '\"").startswith("data:") for part in css.split("url(")[1:]
    )


def test_template_has_no_hardcoded_business_text() -> None:
    template = (ROOT / "site" / "template.html").read_text(encoding="utf-8")
    page = Page(template)
    assert page.first("body")["data-client"] == "{{slug}}"
    assert page.first("body")["data-api"] == "{{api_url}}"


# --- build() on disk ----------------------------------------------------------------------


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    clients = tmp_path / "clients"
    industries = tmp_path / "industries"
    (clients / "acme-co").mkdir(parents=True)
    industries.mkdir()
    (clients / "acme-co" / "config.yaml").write_text(yaml.safe_dump(CLIENT), encoding="utf-8")
    (industries / "test_pack.yaml").write_text(yaml.safe_dump(PACK), encoding="utf-8")
    return clients, industries


def run_build(dirs: tuple[Path, Path], out: Path, slug: str = "acme-co", **kw: Any) -> Path:
    clients, industries = dirs
    return build.build(slug, out_dir=out, clients_dir=clients, industries_dir=industries, **kw)


def test_build_creates_missing_out_parents(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "does" / "not" / "exist"
    target = run_build(dirs, out)
    assert target == out / "acme-co"
    assert sorted(p.name for p in target.iterdir()) == ["chat.js", "index.html", "styles.css"]
    assert (target / "chat.js").read_bytes() == (ROOT / "site" / "chat.js").read_bytes()
    assert (target / "styles.css").read_bytes() == (ROOT / "site" / "styles.css").read_bytes()


def test_rebuild_replaces_old_output(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "out"
    target = run_build(dirs, out)
    (target / "stale.html").write_text("old", encoding="utf-8")
    (target / "sub").mkdir()
    (target / "index.html").write_text("old", encoding="utf-8")
    target = run_build(dirs, out)
    assert sorted(p.name for p in target.iterdir()) == ["chat.js", "index.html", "styles.css"]
    assert "Acme Co" in (target / "index.html").read_text(encoding="utf-8")


def test_rebuild_leaves_sibling_folders_alone(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "other-client").mkdir(parents=True)
    (out / "other-client" / "index.html").write_text("keep", encoding="utf-8")
    run_build(dirs, out)
    assert (out / "other-client" / "index.html").read_text(encoding="utf-8") == "keep"


def test_build_into_clients_dir_does_not_delete_config(dirs: tuple[Path, Path]) -> None:
    clients, _ = dirs
    config_file = clients / "acme-co" / "config.yaml"
    try:
        run_build(dirs, clients)
    except (build.BuildError, OSError):
        pass  # refusing to build there is fine too
    assert config_file.is_file()


@pytest.mark.parametrize("slug", ["../acme-co", "..", "acme-co/../../x", "/abs/x", "ACME"])
def test_traversal_slug_rejected_and_writes_nothing(
    dirs: tuple[Path, Path], tmp_path: Path, slug: str
) -> None:
    out = tmp_path / "out"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    with pytest.raises(ConfigError):
        run_build(dirs, out, slug=slug)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_api_from_env(
    dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_URL", " https://env.example.com/ ")
    target = run_build(dirs, tmp_path / "out")
    page = Page((target / "index.html").read_text(encoding="utf-8"))
    assert page.first("body")["data-api"] == "https://env.example.com"


def test_bad_env_api_fails_before_writing(
    dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_URL", "javascript:alert(1)")
    out = tmp_path / "out"
    with pytest.raises(build.BuildError):
        run_build(dirs, out)
    assert not out.exists()


def test_output_is_utf8_with_lf(dirs: tuple[Path, Path], tmp_path: Path) -> None:
    clients, _ = dirs
    data = dict(CLIENT, name="Zoë 東京 🚀")
    (clients / "acme-co" / "config.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    raw = (run_build(dirs, tmp_path / "out") / "index.html").read_bytes()
    assert b"\r\n" not in raw
    assert "Zoë 東京 🚀".encode() in raw
    assert b'<meta charset="utf-8">' in raw


# --- CLI ----------------------------------------------------------------------------------


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "API_URL"}
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, str(ROOT / "build.py"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
        check=False,
    )


def test_cli_from_other_cwd(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "out"
    result = run_cli(["riverside-plumbing", "--out", str(out)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    target = out / "riverside-plumbing"
    assert sorted(p.name for p in target.iterdir()) == ["chat.js", "index.html", "styles.css"]


def test_cli_default_out_is_relative_to_cwd(tmp_path: Path) -> None:
    result = run_cli(["riverside-plumbing"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dist" / "riverside-plumbing" / "index.html").is_file()


@pytest.mark.parametrize(
    "args",
    [
        ["../riverside-plumbing"],
        ["no-such-client"],
        ["riverside-plumbing", "--api", "javascript:alert(1)"],
        ["riverside-plumbing", "--api", 'http://x.example"'],
        ["riverside-plumbing", "--api", "data:text/html,x"],
        ["riverside-plumbing", "--api", " "],
    ],
)
def test_cli_errors_exit_1_cleanly(tmp_path: Path, args: list[str]) -> None:
    result = run_cli([*args, "--out", str(tmp_path / "out")], cwd=tmp_path)
    assert result.returncode == 1
    assert result.stderr.startswith("Build failed:")
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_cli_out_is_a_file(tmp_path: Path) -> None:
    out = tmp_path / "file.txt"
    out.write_text("x", encoding="utf-8")
    result = run_cli(["riverside-plumbing", "--out", str(out)], cwd=tmp_path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert out.read_text(encoding="utf-8") == "x"
