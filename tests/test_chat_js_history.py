"""Runs site/chat.js under Node with a tiny fake DOM to check the chat history it sends.

Skipped when Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 - runs Node on this repo's own file
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r"""
const fs = require("fs");
function makeEl(tag) {
  return {
    tag, attrs: {}, children: [], childNodes: [], listeners: {}, hidden: false, value: "",
    textContent: "", scrollTop: 0, scrollHeight: 0, className: "", disabled: false,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    appendChild(c) { this.children.push(c); this.childNodes.push(c); return c; },
    addEventListener(t, f) { this.listeners[t] = f; },
    focus() {}, remove() {}, reset() {},
  };
}
const created = [];
const body = makeEl("body");
body.attrs = {"data-client": "acme-co", "data-api": "http://api.test", "data-phone": "555"};
global.document = {
  body, activeElement: null,
  createElement(tag) { const e = makeEl(tag); created.push(e); return e; },
  createElementNS(ns, tag) { return makeEl(tag); },
  createTextNode(t) { return {text: t}; },
  querySelectorAll() { return []; },
  addEventListener() {},
};
const sent = [];
global.fetch = function (url, opts) {
  const payload = JSON.parse(opts.body);
  sent.push(payload);
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({reply: "answer " + sent.length}),
  });
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const form = created.find((e) => e.tag === "form");
const input = created.find((e) => e.tag === "input");
(async function () {
  for (let i = 0; i < 15; i++) {
    input.value = "question " + i;
    form.listeners.submit({preventDefault() {}});
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  }
  console.log(JSON.stringify(sent));
})();
"""


def test_history_capped_and_starts_with_user(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [node, str(harness), str(ROOT / "site" / "chat.js")],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    sent = json.loads(result.stdout)
    assert len(sent) == 15
    for payload in sent:
        assert payload["client"] == "acme-co"
        messages = payload["messages"]
        assert 1 <= len(messages) <= 20
        assert messages[0]["role"] == "user"
        assert messages[-1]["role"] == "user"
    # By the 11th turn the history (21 messages) had to be trimmed.
    assert sent[-1]["messages"][-1]["content"] == "question 14"
    assert sent[-1]["messages"][0]["content"].startswith("question ")
