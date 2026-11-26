"""Run a client's eval questions against the real bot and score the replies.

Usage (from the repo root, venv active):

    python -m evals.run <slug> [--runs N] [--only TEXT] [--env-file .env]

Each case in evals/<slug>.yaml is sent through the same path /chat uses
(load_config -> build_system_prompt -> llm.reply) and checked with simple,
case-insensitive text rules. This makes real API calls, so it is not part of pytest/CI.

Case format (a YAML list):

    - question: How much is your call-out fee?   # required
      history:                                   # optional earlier turns
        - {role: user, content: Hi}
        - {role: assistant, content: Hello! How can I help?}
      must_include: ["$85"]          # every item must appear
      must_include_any: ["a", "b"]   # at least one must appear
      must_not_include: ["$"]        # none may appear
      note: why this case matters    # optional, shown on failure

Every case needs at least one of the three checks. Exit code is 0 only if every run of
every case passes.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from backend import llm
from backend.config_loader import ConfigError, load_config
from backend.prompt import build_system_prompt

EVALS_DIR = Path(__file__).resolve().parent
CHECK_KEYS = ("must_include", "must_include_any", "must_not_include")
ALLOWED_KEYS = {"question", "history", "note", *CHECK_KEYS}

ReplyFn = Callable[[str, list[dict[str, str]]], str]


class EvalFileError(Exception):
    """The eval file is missing or malformed."""


@dataclass(frozen=True)
class Case:
    question: str
    history: tuple[dict[str, str], ...] = ()
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()
    note: str = ""


@dataclass
class Result:
    case: Case
    reply: str
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def _str_list(raw: object, key: str, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str | int | float) for x in raw):
        raise EvalFileError(f"{where}: '{key}' must be a list of strings")
    items = tuple(str(x).strip() for x in raw)
    if any(not x for x in items):
        raise EvalFileError(f"{where}: '{key}' contains an empty string")
    return items


def _history(raw: object, where: str) -> tuple[dict[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise EvalFileError(f"{where}: 'history' must be a list")
    turns: list[dict[str, str]] = []
    for turn in raw:
        if (
            not isinstance(turn, dict)
            or turn.get("role") not in ("user", "assistant")
            or not isinstance(turn.get("content"), str)
            or not turn["content"].strip()
        ):
            raise EvalFileError(f"{where}: each history turn needs role user/assistant and content")
        turns.append({"role": turn["role"], "content": turn["content"]})
    if turns and turns[0]["role"] != "user":
        raise EvalFileError(f"{where}: history must start with a user turn")
    if turns and turns[-1]["role"] != "assistant":
        raise EvalFileError(f"{where}: history must end with an assistant turn")
    return tuple(turns)


def parse_cases(data: Any, source: str = "eval file") -> list[Case]:
    """Validate parsed YAML and return the cases. Raises EvalFileError on any problem."""
    if data is None:
        return []
    if not isinstance(data, list):
        raise EvalFileError(f"{source}: must be a YAML list of cases")
    cases: list[Case] = []
    for i, raw in enumerate(data, start=1):
        where = f"{source} case {i}"
        if not isinstance(raw, dict):
            raise EvalFileError(f"{where}: must be a mapping")
        unknown = set(raw) - ALLOWED_KEYS
        if unknown:
            raise EvalFileError(f"{where}: unknown keys {sorted(unknown)}")
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            raise EvalFileError(f"{where}: 'question' must be a non-empty string")
        checks = {k: _str_list(raw.get(k), k, where) for k in CHECK_KEYS}
        if not any(checks.values()):
            raise EvalFileError(f"{where}: needs at least one of {', '.join(CHECK_KEYS)}")
        note = raw.get("note") or ""
        if not isinstance(note, str):
            raise EvalFileError(f"{where}: 'note' must be a string")
        cases.append(
            Case(
                question=question.strip(),
                history=_history(raw.get("history"), where),
                note=note.strip(),
                **checks,
            )
        )
    return cases


def load_cases(path: Path) -> list[Case]:
    if not path.is_file():
        raise EvalFileError(f"no eval file at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvalFileError(f"{path.name}: invalid YAML ({exc})") from exc
    return parse_cases(data, path.name)


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def check_reply(case: Case, reply: str) -> list[str]:
    """Return a list of problems; empty means the reply passes."""
    text = _norm(reply)
    problems = [f"missing {item!r}" for item in case.must_include if _norm(item) not in text]
    if case.must_include_any and not any(_norm(i) in text for i in case.must_include_any):
        problems.append(f"none of {list(case.must_include_any)}")
    problems += [f"contains {item!r}" for item in case.must_not_include if _norm(item) in text]
    return problems


def run_case(case: Case, system: str, reply_fn: ReplyFn) -> Result:
    messages = [*case.history, {"role": "user", "content": case.question}]
    try:
        reply = reply_fn(system, [dict(m) for m in messages])
    except llm.LLMError as exc:
        return Result(case, "", [f"LLM error: {exc}"])
    return Result(case, reply, check_reply(case, reply))


def _short(text: str, limit: int = 300) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def main(argv: Sequence[str] | None = None, reply_fn: ReplyFn | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.split("\n")[0])
    parser.add_argument("slug", help="client slug, e.g. the folder name under clients/")
    parser.add_argument(
        "--runs", type=int, default=1, help="times to ask each question (default 1)"
    )
    parser.add_argument("--only", default="", help="only run cases whose question contains TEXT")
    parser.add_argument("--file", type=Path, help="eval file (default evals/<slug>.yaml)")
    parser.add_argument(
        "--env-file", default=".env", help="load env vars from this file if present"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print every reply")
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if reply_fn is None:
        if os.path.isfile(args.env_file):
            load_dotenv(args.env_file, override=False)
        reply_fn = llm.reply

    try:
        config = load_config(args.slug)
        cases = load_cases(args.file or EVALS_DIR / f"{args.slug}.yaml")
    except (ConfigError, EvalFileError) as exc:
        print(f"Eval setup failed: {exc}", file=sys.stderr)
        return 2

    if args.only:
        cases = [c for c in cases if args.only.casefold() in c.question.casefold()]
    if not cases:
        print("No eval cases to run.", file=sys.stderr)
        return 2

    system = build_system_prompt(config)
    total = passed = 0
    for n, case in enumerate(cases, start=1):
        for run in range(1, args.runs + 1):
            result = run_case(case, system, reply_fn)
            total += 1
            passed += result.passed
            tag = "PASS" if result.passed else "FAIL"
            suffix = f" (run {run}/{args.runs})" if args.runs > 1 else ""
            print(f"[{tag}] {n}. {case.question}{suffix}")
            if not result.passed:
                print(f"       problems: {'; '.join(result.problems)}")
                if case.note:
                    print(f"       note:     {case.note}")
            if result.reply and (args.verbose or not result.passed):
                print(f"       reply:    {_short(result.reply)}")

    pct = round(100 * passed / total)
    print(f"\nScore: {passed}/{total} ({pct}%) for {config.name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
