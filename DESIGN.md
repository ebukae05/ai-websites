# Design

How the pieces fit together, and why.

## Flow
1. `build.py` reads `clients/<name>/config.yaml` and fills `site/template.html` -> `dist/<name>/`.
2. The visitor's chat bubble (`site/chat.js`) posts to `backend/main.py` `/chat`.
3. `main.py` loads the client config (`config_loader.py`), builds the instructions (`prompt.py`),
   and asks the model (`llm.py`).
4. Contact details go to `/lead` -> `leads.py` saves them and emails the owner.
