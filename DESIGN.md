# Design

How the pieces fit together, and why.

## 1. Flow

1. `build.py` reads `clients/<name>/config.yaml` and fills `site/template.html` -> `dist/<name>/`.
2. The visitor's chat bubble (`site/chat.js`) posts to `backend/main.py` `/chat`.
3. `main.py` loads the client config (`config_loader.py`), builds the instructions (`prompt.py`),
   and asks the model (`llm.py`).
4. Contact details go to `/lead` -> `leads.py` saves them and emails the owner.

## 2. Components

| File | Job |
|---|---|
| `backend/main.py` | HTTP endpoints: `/chat`, `/lead`, `/health` |
| `backend/llm.py` | The only caller of the AI model |
| `backend/prompt.py` | Builds the bot's instructions from a client config |
| `backend/leads.py` | Saves leads and emails the owner |
| `backend/config_loader.py` | Loads and validates `clients/<name>/config.yaml` |
| `site/*` | One-page layout, styles, chat bubble |
| `build.py` | Template + config -> finished site in `dist/` |

## 3. Data

### 3.1 Client config (`clients/<slug>/config.yaml`)

One file per business. Data only; validated by `config_loader.py` at load time.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Business name shown on the site and used by the bot |
| `industry` | string | yes | Selects the industry pack (prompt wording, default FAQs, site copy). v1 supports one pack: `home_services` |
| `phone` | string | yes | Shown on the site; the bot offers it as a fallback |
| `email` | string | yes | Where new leads are sent |
| `hours` | map | yes | Day -> opening hours, e.g. `mon: "8:00-17:00"` |
| `services` | list | yes | What the business offers |
| `faqs` | list | no | `{question, answer}` pairs the bot answers from |

An unknown `industry` value is a load error, not a silent default.
