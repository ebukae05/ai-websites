# ai-websites

One-page websites for local businesses, each with an AI chat bot that answers customer
questions and captures leads.

## Layout

```
backend/        THE SERVER: the AI chat that runs behind every site
  main.py         the doors: /chat, /lead, /health (receives requests)
  llm.py          the only file that talks to the AI model
  prompt.py       builds the bot's instructions from a client's info
  leads.py        saves leads and emails them to the owner
  config_loader.py  reads and checks each client's info file
site/           THE FRONTEND: what the customer sees
  template.html   the one-page website layout
  styles.css      how it looks
  chat.js         the chat bubble
clients/        DATA, not code: one folder per business
  <client>/config.yaml  name, services, hours, FAQs, phone...
evals/          test questions the bot must get right, per client
tests/          automated tests (mirrors backend/)
build.py        combines template + config -> a finished site in dist/
dist/           the finished sites (generated, NOT committed)
.claude/agents/ dev-team agents (tools for building, not part of the product)
.github/workflows/ci.yml  checks every PR automatically
```

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
pre-commit install
copy .env.example .env   # then fill in the keys
```
