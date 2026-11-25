# Tasks

## Phase 0: Foundations

- [x] Dev-team agents and `.claude/settings.json` in place
- [x] `.gitignore` covers `.env`, `dist/`, `.venv/`; `.env.example` committed
- [x] Folder skeleton matches README (backend/, site/, clients/, evals/, tests/)
- [x] Dev tooling: ruff, mypy, pytest, pre-commit with gitleaks
- [x] CI runs lint, type check, tests and secret scan on every PR
- [x] CLAUDE.md written
- [x] DESIGN.md 3.1 client config includes `industry` (v1 pack: `home_services`)

## Phase 1: Build

- [x] Fill in riverside-plumbing config.yaml
- [x] Implement config_loader.py
- [x] Implement prompt.py and llm.py
- [x] Implement /chat, /lead, /health in main.py
- [x] Implement leads.py
- [ ] Build template.html, styles.css, chat.js
- [ ] Implement build.py
- [ ] [CHECK] Human opens the demo page and tries the chat bot
- [ ] Write evals for riverside-plumbing
