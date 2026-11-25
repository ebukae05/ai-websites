# Dev Team Workflow

Four Claude Code subagents that take a business problem to a reviewed PR.

```
Problem / audit notes
      │
      ▼
 Architect ──► docs/PRD.md + docs/DESIGN.md ──► YOU approve (gate 1)
                                                     │
                                                     ▼
                                                 Engineer ──► branch + PR
                                                   ▲   ▲          │
                             failures (max 3 loops)│   │          ▼
                                                   │   │       Tester ──► docs/TEST_REPORT.md
                                                   │   │          │ PASS
                                     change requests   │          ▼
                                                       └────── Reviewer ──► docs/REVIEW.md
                                                                  │ APPROVE
                                                                  ▼
                                                          YOU merge (gate 2) ──► auto-deploy
```

## Setup (once per repo)

1. Copy the `.claude/` folder into the repo root (the `agents/` folder and `settings.json`).
2. Make sure `.env` is in `.gitignore`.
3. Open Claude Code in the repo and run `/agents` to confirm all four show up.

## Running it

Paste this into Claude Code, replacing the problem:

```
Run our dev team workflow from WORKFLOW.md for this problem:
"<business problem or feature>"

1. Use the architect agent to write docs/PRD.md and docs/DESIGN.md. Stop for my approval.
2. After I approve, use the engineer agent to build it on a new branch.
3. Use the tester agent to try to break it. If it FAILS, send the report to the engineer
   and repeat. Stop and ask me after 3 failed loops.
4. When the tester PASSES, use the reviewer agent. If it requests changes, send them to
   the engineer, then rerun the tester and reviewer.
5. When the reviewer APPROVES, stop and tell me the PR is ready for me to merge.
```

## Rules

- You own two decisions: approving the PRD and merging. Agents never push or merge (blocked in settings.json).
- Tester and reviewer judge the code, not the engineer's explanation.
- `.env` is unreadable to agents. Secrets live in environment variables only.
- For code you're trying to learn (hand-written zones in CLAUDE.md), the engineer gives hints instead of code.

## First job

"Build make_demo.py: given a business website URL, scrape its public pages, index them with our
RAG pipeline (reuse the L'SPACE bot's ingestion), take a homepage screenshot, and serve a demo
page with a chat widget that answers from that index. If the bot can't answer, it collects the
visitor's name and phone number. Must work end to end in under 15 minutes per business."
