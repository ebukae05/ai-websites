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
| `instagram_url` | string | no | Link shown in the footer; must start with `https://` |
| `email` | string | yes | Where new leads are sent |
| `hours` | map | yes | Day -> opening hours, e.g. `mon: "8:00-17:00"` |
| `services` | list | yes | What the business offers |
| `faqs` | list | no | `{question, answer}` pairs the bot answers from |

An unknown `industry` value is a load error, not a silent default.

### 3.2 Industry packs (`backend/industries/<industry>.yaml`)

One YAML file per supported `industry` value; v1 ships `home_services.yaml`. Data only.
The template, `build.py`, `prompt.py` and the rest of the backend stay industry-neutral:
any industry-specific wording lives in a pack, and any business-specific wording lives in
the client config.

| Field | Type | Notes |
|---|---|---|
| `bot_role` | string | One-line description of the bot's job, used by `prompt.py` |
| `prompt_rules` | list | Extra instructions for the bot in this industry |
| `default_faqs` | list | `{question, answer}` pairs used when the client config has none for that question |
| `site.tagline` | string | Hero line under the business name; may use `{name}` |
| `site.services_heading` | string | Heading above the services list |
| `site.cta` | string | Call-to-action text on the contact button |

`config_loader.py` loads the pack named by `industry`; an unknown value or a missing pack
file is a load error. Client `faqs` come before the pack's `default_faqs`.

`build.py <slug>` writes `dist/<slug>/index.html`, `styles.css` and `chat.js`. The page
tells `chat.js` the client slug and the backend URL (default `http://localhost:8000`).
