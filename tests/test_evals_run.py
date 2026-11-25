"""Tests for evals/run.py (no network: the model is replaced by a fake)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import llm
from evals import run
from evals.run import Case, EvalFileError, check_reply, parse_cases


def test_check_reply_rules_are_case_and_space_insensitive() -> None:
    case = Case(
        question="q",
        must_include=["$85", "Call  Us"],  # type: ignore[arg-type]
        must_include_any=("name", "phone"),
        must_not_include=("guarantee",),
    )
    assert check_reply(case, "The fee is $85. call us\nany time for your PHONE") == []
    problems = check_reply(case, "We guarantee it")
    assert "missing '$85'" in problems
    assert "none of ['name', 'phone']" in problems
    assert "contains 'guarantee'" in problems


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"question": "x"}, "must be a YAML list"),
        (["x"], "must be a mapping"),
        ([{"question": "", "must_include": ["a"]}], "non-empty string"),
        ([{"question": "q"}], "needs at least one"),
        ([{"question": "q", "must_include": "a"}], "list of strings"),
        ([{"question": "q", "must_include": [""]}], "empty string"),
        ([{"question": "q", "must_inclde": ["a"]}], "unknown keys"),
        ([{"question": "q", "must_include": ["a"], "history": [{"role": "bot"}]}], "history"),
        (
            [
                {
                    "question": "q",
                    "must_include": ["a"],
                    "history": [{"role": "assistant", "content": "hi"}],
                }
            ],
            "start with a user",
        ),
    ],
)
def test_parse_cases_rejects_bad_files(data: object, message: str) -> None:
    with pytest.raises(EvalFileError, match=message):
        parse_cases(data)


def test_parse_cases_accepts_full_case_and_empty_file() -> None:
    assert parse_cases(None) == []
    assert parse_cases([]) == []
    [case] = parse_cases(
        [
            {
                "question": " Fee? ",
                "history": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                "must_include": [85],
                "note": "why",
            }
        ]
    )
    assert case.question == "Fee?"
    assert case.must_include == ("85",)
    assert len(case.history) == 2


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_main_scores_and_sends_history(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(
        tmp_path,
        "- question: Fee?\n"
        "  history: [{role: user, content: hi}, {role: assistant, content: hello}]\n"
        "  must_include: ['$85']\n"
        "- question: Secret?\n"
        "  must_not_include: ['$85']\n"
        "  note: must not leak\n",
    )
    seen: list[list[dict[str, str]]] = []

    def fake(system: str, messages: list[dict[str, str]]) -> str:
        seen.append(messages)
        assert system
        return "It is $85."

    code = run.main(["riverside-plumbing", "--file", str(path), "--runs", "2"], reply_fn=fake)
    out = capsys.readouterr().out
    assert code == 1
    assert "Score: 2/4 (50%)" in out
    assert "note:     must not leak" in out
    assert seen[0][0] == {"role": "user", "content": "hi"}
    assert seen[0][-1] == {"role": "user", "content": "Fee?"}


def test_main_all_pass_and_only_filter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(
        tmp_path,
        "- {question: 'Fee?', must_include: [ok]}\n- {question: Other, must_include: [x]}\n",
    )
    code = run.main(
        ["riverside-plumbing", "--file", str(path), "--only", "fee"], reply_fn=lambda s, m: "OK"
    )
    assert code == 0
    assert "Score: 1/1" in capsys.readouterr().out


def test_main_llm_error_counts_as_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, "- {question: 'Fee?', must_include: [ok]}\n")

    def broken(system: str, messages: list[dict[str, str]]) -> str:
        raise llm.LLMError("AI model request failed: APITimeoutError")

    assert run.main(["riverside-plumbing", "--file", str(path)], reply_fn=broken) == 1
    assert "LLM error" in capsys.readouterr().out


@pytest.mark.parametrize("slug", ["no-such-client", "../etc"])
def test_main_bad_slug_exits_2(slug: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main([slug], reply_fn=lambda s, m: "") == 2
    assert "Eval setup failed" in capsys.readouterr().err


def test_main_empty_eval_file_exits_2(tmp_path: Path) -> None:
    path = _write(tmp_path, "[]\n")
    assert run.main(["riverside-plumbing", "--file", str(path)], reply_fn=lambda s, m: "") == 2


def test_real_sample_eval_file_is_valid() -> None:
    run.load_cases(run.EVALS_DIR / "riverside-plumbing.yaml")
