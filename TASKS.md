# Tasks

## Phase 0: Foundations

- [ ] Dev-team agents and `.claude/settings.json` in place
- [ ] `.gitignore` covers `.env`, `dist/`, `.venv/`; `.env.example` committed
- [ ] Folder skeleton matches README (backend/, site/, clients/, evals/, tests/)
- [ ] Dev tooling: ruff, mypy, pytest, pre-commit with gitleaks
- [ ] CI runs lint, type check, tests and secret scan on every PR
- [ ] CLAUDE.md written
- [ ] DESIGN.md 3.1 client config includes `industry` (v1 pack: `home_services`)

## Phase 1: Build

- [ ] Fill in riverside-plumbing config.yaml
- [ ] Implement config_loader.py
- [ ] Implement prompt.py and llm.py
- [ ] Implement /chat, /lead, /health in main.py
- [ ] Implement leads.py
- [ ] Build template.html, styles.css, chat.js
- [ ] Implement build.py
- [ ] Write evals for riverside-plumbing
