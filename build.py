"""Combines site/template.html + clients/<name>/config.yaml -> dist/<name>/ (a finished site).

Usage:  python build.py <slug> [--api URL] [--out DIR]

--api defaults to env API_URL, else http://localhost:8000. --out defaults to dist.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from backend.config_loader import DAYS, ClientConfig, ConfigError, load_config
from backend.prompt import DAY_NAMES

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"
DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_OUT_DIR = Path("dist")
STATIC_FILES = ("styles.css", "chat.js")
BUILD_OUTPUTS = ("index.html", *STATIC_FILES)
SOURCE_SUFFIXES = {".yaml", ".yml", ".py"}
TOKEN_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class BuildError(Exception):
    """The site could not be built (bad template, bad option, unfilled token)."""


def esc(value: str) -> str:
    """HTML-escape text for use in element content and quoted attributes."""
    return html.escape(value, quote=True)


def phone_href(phone: str) -> str:
    """The main number only (no extension), as digits with an optional leading +."""
    main_number = re.split(r"ext|x|[,;]", phone, maxsplit=1, flags=re.IGNORECASE)[0]
    digits = re.sub(r"\D", "", main_number)
    return ("+" + digits) if main_number.strip().startswith("+") else digits


def render_services(config: ClientConfig) -> str:
    return "\n".join(f"          <li>{esc(s)}</li>" for s in config.services)


def render_hours(config: ClientConfig) -> str:
    rows = []
    for day in DAYS:
        hours = config.hours.get(day, "Not listed")
        rows.append(
            f'            <tr><th scope="row">{esc(DAY_NAMES[day])}</th><td>{esc(hours)}</td></tr>'
        )
    return "\n".join(rows)


def render_faqs(config: ClientConfig) -> str:
    return "\n".join(
        f"          <details><summary>{esc(f.question)}</summary><p>{esc(f.answer)}</p></details>"
        for f in config.faqs
    )


def render_instagram_link(config: ClientConfig) -> str:
    """Footer link to the business's Instagram page, or nothing if it has none."""
    if not config.instagram_url:
        return ""
    return (
        f' &middot; <a href="{esc(config.instagram_url)}" target="_blank" '
        'rel="noopener noreferrer">Instagram</a>'
    )


def check_api_url(api_url: str) -> str:
    url = api_url.strip().rstrip("/")
    match = re.fullmatch(r"(https?)://([^\s\"'<>?#]+)", url, flags=re.IGNORECASE)
    if not match:
        raise BuildError(
            f"--api must be an http:// or https:// URL without a query or #fragment, "
            f"got {api_url!r}."
        )
    return f"{match.group(1).lower()}://{match.group(2)}"


def render(
    config: ClientConfig,
    api_url: str = DEFAULT_API_URL,
    template: str | None = None,
    year: int | None = None,
) -> str:
    """Fill the template with escaped values from `config`. Raises BuildError on bad input."""
    if template is None:
        template = (SITE_DIR / "template.html").read_text(encoding="utf-8")
    site = config.pack.site
    values = {
        "slug": esc(config.slug),
        "api_url": esc(check_api_url(api_url)),
        "name": esc(config.name),
        "tagline": esc(site.tagline.replace("{name}", config.name)),
        "phone": esc(config.phone),
        "phone_href": esc(phone_href(config.phone)),
        "cta": esc(site.cta),
        "services_heading": esc(site.services_heading),
        "services_list": render_services(config),
        "hours_rows": render_hours(config),
        "faq_items": render_faqs(config),
        "faq_hidden": "" if config.faqs else "hidden",
        "instagram_link": render_instagram_link(config),
        "year": str(year if year is not None else date.today().year),
    }
    # Check the template, not the output: a value may legitimately contain "{{...}}".
    unknown = sorted({m.group(1) for m in TOKEN_RE.finditer(template)} - set(values))
    if unknown:
        raise BuildError(f"Template has unfilled tokens: {', '.join(unknown)}")
    return TOKEN_RE.sub(lambda m: values[m.group(1)], template)


def _is_previous_build(folder: Path) -> bool:
    """True if `folder` holds the files a build writes and no source files (.yaml, .py)."""
    names = {p.name for p in folder.iterdir()}
    if not set(BUILD_OUTPUTS) <= names:
        return False
    return not any(p.suffix in SOURCE_SUFFIXES for p in folder.rglob("*"))


def _clear_previous_build(target: Path, site_dir: Path) -> None:
    """Delete `target` only if it is an earlier build; refuse anything else."""
    if not target.exists():
        return
    if not target.is_dir() or target.resolve() in {site_dir.resolve(), ROOT}:
        raise BuildError(f"Refusing to overwrite {target}: it is not a previous build.")
    if not any(target.iterdir()):
        return  # empty folder: nothing to delete
    if not _is_previous_build(target):
        raise BuildError(f"Refusing to overwrite {target}: it is not a previous build.")
    shutil.rmtree(target)


def build(
    slug: str,
    api_url: str | None = None,
    out_dir: Path = DEFAULT_OUT_DIR,
    clients_dir: Path | None = None,
    industries_dir: Path | None = None,
    site_dir: Path = SITE_DIR,
) -> Path:
    """Build `<out_dir>/<slug>/` (index.html, styles.css, chat.js) and return that folder."""
    config = load_config(slug, clients_dir=clients_dir, industries_dir=industries_dir)
    api = api_url or os.environ.get("API_URL", "").strip() or DEFAULT_API_URL
    page = render(config, api, template=(site_dir / "template.html").read_text(encoding="utf-8"))
    target = out_dir / config.slug  # slug is validated by load_config: no path tricks
    _clear_previous_build(target, site_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(page, encoding="utf-8", newline="\n")
    for name in STATIC_FILES:
        shutil.copyfile(site_dir / name, target / name)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a client's one-page website.")
    parser.add_argument("slug", help="client folder name under clients/")
    parser.add_argument("--api", default=None, help=f"backend URL (default {DEFAULT_API_URL})")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="output folder")
    args = parser.parse_args(argv)
    try:
        target = build(args.slug, api_url=args.api, out_dir=Path(args.out))
    except (ConfigError, BuildError, OSError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Built {target / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
