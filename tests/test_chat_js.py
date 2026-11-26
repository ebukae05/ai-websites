"""Tester probes for site/chat.js.

Static checks read the file as text. Behaviour checks run chat.js under node with a tiny
fake DOM and a fake fetch (no network); they are skipped when node is not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # noqa: S404 - runs node on this repo's own chat.js
from pathlib import Path
from typing import Any

import pytest

from backend import main

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "site" / "chat.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def source() -> str:
    return CHAT_JS.read_text(encoding="utf-8")


def code_only(js: str) -> str:
    """Drop // line comments that start a line or follow code after whitespace."""
    return "\n".join(re.sub(r"(^|\s)//(?!/).*$", "", line) for line in js.splitlines())


# --- static checks ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        r"\.innerHTML\s*[+]?=",
        r"\.outerHTML\s*[+]?=",
        r"insertAdjacentHTML\s*\(",
        r"document\.write(?:ln)?\s*\(",
        r"\beval\s*\(",
        r"new\s+Function\s*\(",
        r"setTimeout\s*\(\s*[\"']",
        r"createContextualFragment",
        r"DOMParser",
    ],
)
def test_no_html_injection_sinks(source: str, pattern: str) -> None:
    assert re.search(pattern, code_only(source)) is None


def test_history_cap_matches_backend(source: str) -> None:
    match = re.search(r"var\s+MAX_HISTORY\s*=\s*(\d+)\s*;", source)
    assert match is not None
    assert int(match.group(1)) == 20 == main.MAX_MESSAGES
    assert source.count("history.length > MAX_HISTORY") >= 2


def test_char_cap_matches_backend(source: str) -> None:
    match = re.search(r"var\s+MAX_CHARS\s*=\s*(\d+)\s*;", source)
    assert match is not None
    assert int(match.group(1)) == main.MAX_CONTENT_CHARS


def test_reads_settings_from_body_data_attributes(source: str) -> None:
    for attr in ("data-client", "data-api", "data-phone", "data-name"):
        assert f'getAttribute("{attr}")' in source


def test_lead_field_limits_match_backend(source: str) -> None:
    fields = main.LeadRequest.model_fields
    limits = {"name": 100, "phone": 200, "email": 200}
    for name, limit in limits.items():
        assert re.search(rf'field\([^)]*"{name}"[^)]*maxlength:\s*"{limit}"', source), name
        assert name in fields


def test_lead_requires_name_and_phone_or_email(source: str) -> None:
    assert "if (!payload.name)" in source
    assert "if (!payload.phone && !payload.email)" in source


def test_no_external_urls_in_script(source: str) -> None:
    urls = set(re.findall(r"https?://[^\s\"')]+", source))
    assert urls <= {"http://www.w3.org/2000/svg", "http://localhost:8000"}


def test_node_syntax_check() -> None:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(  # noqa: S603 - fixed args
        [NODE, "--check", str(CHAT_JS)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr


# --- behaviour under node -----------------------------------------------------------------

HARNESS = r"""
"use strict";
const fs = require("fs");
const scenario = JSON.parse(process.argv[3]);
const all = [];
class Node {
  constructor(tag) {
    this.tagName = tag; this.attrs = {}; this.children = []; this.listeners = {};
    this.hidden = false; this.disabled = false; this.value = ""; this.className = "";
    this._text = ""; this.scrollTop = 0; this.scrollHeight = 0; this.parent = null;
    all.push(this);
  }
  get childNodes() { return this.children; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set innerHTML(v) { throw new Error("innerHTML used"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML used"); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() { global.document.activeElement = this; }
  reset() { all.filter(n => n.parent && isIn(n, this)).forEach(n => { n.value = ""; }); }
  fire(t) { (this.listeners[t] || []).forEach(f => f({ preventDefault() {}, key: "" })); }
}
function isIn(n, root) {
  for (let p = n.parent; p; p = p.parent) if (p === root) return true;
  return false;
}
const body = new Node("body");
Object.assign(body.attrs, scenario.body);
global.document = {
  body, activeElement: null,
  createElement: t => new Node(t),
  createElementNS: (_, t) => new Node(t),
  createTextNode: t => { const n = new Node("#text"); n._text = t; return n; },
  addEventListener() {},
  querySelectorAll: () => [],
};
const requests = [];
let replyIndex = 0;
global.fetch = (url, opts) => {
  requests.push({ url, body: JSON.parse(opts.body) });
  const r = scenario.responses[Math.min(replyIndex++, scenario.responses.length - 1)];
  if (r === "network") return Promise.reject(new Error("offline"));
  const json = () => Promise.resolve(r.json);
  return Promise.resolve({ ok: r.status < 300, status: r.status, json });
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const byClass = c => all.find(n => (n.attrs.class || "").split(" ").includes(c));
const byName = n => all.find(x => x.attrs.name === n);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (const step of scenario.steps) {
    if (step.chat !== undefined) {
      byClass("chat-input").value = step.chat;
      byClass("chat-form").fire("submit");
    } else {
      for (const k of ["name", "phone", "email", "message"]) byName(k).value = step.lead[k] || "";
      byClass("lead-form").fire("submit");
    }
    for (let i = 0; i < 5; i++) await tick();
  }
  const log = byClass("chat-messages").children
    .map(n => ({ cls: n.attrs.class, text: n.textContent }));
  console.log(JSON.stringify({ requests, log, leadStatus: byClass("lead-status").textContent }));
})().catch(e => { console.error(e.stack); process.exit(2); });
"""

BODY = {
    "data-client": "acme-co",
    "data-api": "https://api.example.com/",
    "data-phone": "555-0100",
    "data-name": "Acme Co",
}


def run_scenario(tmp_path: Path, steps: list[dict[str, Any]], responses: list[Any]) -> Any:
    assert NODE is not None
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    scenario = json.dumps({"body": BODY, "steps": steps, "responses": responses})
    result = subprocess.run(  # noqa: S603 - fixed args
        [NODE, str(harness), str(CHAT_JS), scenario],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_history_capped_at_20_and_ends_with_user(tmp_path: Path) -> None:
    steps: list[dict[str, Any]] = [{"chat": f"question {i}"} for i in range(30)]
    out = run_scenario(tmp_path, steps, [{"status": 200, "json": {"reply": "answer"}}])
    assert len(out["requests"]) == 30
    for i, req in enumerate(out["requests"]):
        assert req["url"] == "https://api.example.com/chat"
        assert req["body"]["client"] == "acme-co"
        msgs = req["body"]["messages"]
        assert 1 <= len(msgs) <= 20
        assert msgs[-1] == {"role": "user", "content": f"question {i}"}
        assert all(set(m) == {"role", "content"} for m in msgs)
        assert all(m["role"] in ("user", "assistant") for m in msgs)


@needs_node
def test_blank_and_long_messages(tmp_path: Path) -> None:
    steps: list[dict[str, Any]] = [{"chat": "   "}, {"chat": "x" * 5000}]
    out = run_scenario(tmp_path, steps, [{"status": 200, "json": {"reply": "ok"}}])
    assert len(out["requests"]) == 1
    assert len(out["requests"][0]["body"]["messages"][0]["content"]) == 2000


@needs_node
def test_html_reply_rendered_as_text(tmp_path: Path) -> None:
    evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    out = run_scenario(tmp_path, [{"chat": evil}], [{"status": 200, "json": {"reply": evil}}])
    texts = [entry["text"] for entry in out["log"]]
    assert texts.count(evil) == 2  # user bubble and bot bubble, both as plain text


@needs_node
@pytest.mark.parametrize(
    ("response", "expect"),
    [
        ({"status": 503, "json": {"detail": "Please call us on 555-0100."}}, "555-0100"),
        ({"status": 500, "json": None}, "555-0100"),
        ({"status": 404, "json": {"detail": "unknown client"}}, "555-0100"),
        ("network", "555-0100"),
    ],
)
def test_errors_show_phone_and_drop_failed_turn(tmp_path: Path, response: Any, expect: str) -> None:
    steps: list[dict[str, Any]] = [{"chat": "first"}, {"chat": "second"}]
    out = run_scenario(tmp_path, steps, [response, {"status": 200, "json": {"reply": "ok"}}])
    errors = [e["text"] for e in out["log"] if "chat-msg-error" in e["cls"]]
    assert errors and expect in errors[0]
    assert "unknown client" not in " ".join(errors)
    # The failed question is not resent as history.
    assert out["requests"][1]["body"]["messages"] == [{"role": "user", "content": "second"}]


@needs_node
@pytest.mark.parametrize(
    ("lead", "sent"),
    [
        ({"name": "", "phone": "555"}, False),
        ({"name": "   ", "email": "a@b.example"}, False),
        ({"name": "Jane"}, False),
        ({"name": "Jane", "phone": "  ", "email": "  "}, False),
        ({"name": "Jane", "phone": "555"}, True),
        ({"name": "Jane", "email": "a@b.example"}, True),
    ],
)
def test_lead_form_requires_name_and_contact(
    tmp_path: Path, lead: dict[str, str], sent: bool
) -> None:
    out = run_scenario(tmp_path, [{"lead": lead}], [{"status": 201, "json": {"ok": True}}])
    assert (len(out["requests"]) == 1) is sent
    if sent:
        req = out["requests"][0]
        assert req["url"] == "https://api.example.com/lead"
        assert req["body"]["client"] == "acme-co"
        assert req["body"]["name"] == lead["name"].strip()
    else:
        assert out["leadStatus"].startswith("Please enter")
