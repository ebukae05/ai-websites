# CLAUDE.md

Project guide for Claude. See README.md for the folder layout.

## Rules
- `backend/llm.py` is the only file that calls the AI model.
- `clients/` holds data only; no code goes there.
- Never commit `.env` or `dist/`.
- Every backend module has a matching test file in `tests/`.
- Design decisions live in DESIGN.md; open work lives in TASKS.md.
