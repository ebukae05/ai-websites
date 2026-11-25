"""Repo rule: code and the site template stay industry- and business-neutral.

DESIGN.md 3.2: industry wording lives only in an industry pack; business wording lives only
in a client config. This scans the neutral files on disk, so new files are covered too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

# Stems of words that belong to one industry. Each pattern is anchored at a word start so
# that unrelated words (for example a "pipeline" or "gitleaks") do not trip it.
INDUSTRY_PATTERNS: dict[str, str] = {
    "plumb": r"plumb",
    "leak": r"\bleak",
    "pipe": r"\bpip(?:e|es|ed|ing)\b",
    "drain": r"\bdrain",
    "home service": r"\bhome[\s_-]*services?\b",
    "trades": r"\btrades(?:people|person|man|men)?\b",
    "repair": r"\brepair",
}


def neutral_files() -> list[Path]:
    files = sorted((ROOT / "backend").rglob("*.py"))
    files.append(ROOT / "build.py")
    files.extend(p for p in sorted((ROOT / "site").rglob("*")) if p.is_file())
    return [p for p in files if "__pycache__" not in p.parts]


def test_scan_covers_expected_files() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in neutral_files()}
    assert {"backend/prompt.py", "backend/llm.py", "build.py", "site/template.html"} <= names
    assert not any(n.startswith(("clients/", "tests/")) or n.endswith(".yaml") for n in names)


@pytest.mark.parametrize("word", sorted(INDUSTRY_PATTERNS))
def test_no_industry_words_in_neutral_files(word: str) -> None:
    pattern = re.compile(INDUSTRY_PATTERNS[word], re.IGNORECASE)
    hits = []
    for path in neutral_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert hits == []


def test_no_client_names_or_slugs_in_neutral_files() -> None:
    terms: list[str] = []
    for config_path in sorted((ROOT / "clients").glob("*/config.yaml")):
        terms.append(config_path.parent.name)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            terms.append(data["name"])
    assert terms, "expected at least one client config"
    hits = [
        f"{path.relative_to(ROOT).as_posix()}: {term}"
        for path in neutral_files()
        for term in terms
        if term.lower() in path.read_text(encoding="utf-8", errors="replace").lower()
    ]
    assert hits == []


def test_patterns_catch_the_words_they_guard() -> None:
    samples = {
        "plumb": "Plumber",
        "leak": "leaking",
        "pipe": "burst pipes",
        "drain": "Drain",
        "home service": "home_services",
        "trades": "local trades",
        "repair": "Repair",
    }
    assert set(samples) == set(INDUSTRY_PATTERNS)
    for word, sample in samples.items():
        assert re.search(INDUSTRY_PATTERNS[word], sample, re.IGNORECASE), (word, sample)
    for innocent in ["pipeline", "gitleaks", "trade-off", "tradesy"]:
        assert not any(re.search(p, innocent, re.IGNORECASE) for p in INDUSTRY_PATTERNS.values())
