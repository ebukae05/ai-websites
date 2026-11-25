"""Tests for backend/main.py."""

import importlib


def test_backend_modules_import() -> None:
    for name in ("main", "llm", "prompt", "leads", "config_loader"):
        importlib.import_module(f"backend.{name}")
