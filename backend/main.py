"""The doors: /chat, /lead, /health (receives requests).

Run locally:  uvicorn backend.main:app --reload --port 8000 --env-file .env
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from backend import leads, llm
from backend.config_loader import ClientConfig, ConfigError, load_config
from backend.prompt import build_system_prompt

logger = logging.getLogger(__name__)

MAX_MESSAGES = 20
MAX_CONTENT_CHARS = 2000

# Module-level hooks so tests (and other deployments) can swap them.
llm_reply = llm.reply
CLIENTS_DIR: Path | None = None  # None = repo clients/ folder
INDUSTRIES_DIR: Path | None = None  # None = backend/industries/

ClientSlug = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(min_length=1, max_length=MAX_CONTENT_CHARS)]

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class ChatRequest(BaseModel):
    client: ClientSlug
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)

    @model_validator(mode="after")
    def last_message_from_user(self) -> ChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


class ChatResponse(BaseModel):
    reply: str


OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class LeadRequest(BaseModel):
    client: ClientSlug
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    phone: OptionalText | None = None
    email: OptionalText | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None

    @model_validator(mode="after")
    def phone_or_email(self) -> LeadRequest:
        if not (self.phone or self.email):
            raise ValueError("give a phone number or an email address")
        return self


class LeadResponse(BaseModel):
    ok: bool


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip() or "*"
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="Website chat backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _load(slug: str) -> ClientConfig:
    try:
        return load_config(slug, clients_dir=CLIENTS_DIR, industries_dir=INDUSTRIES_DIR)
    except ConfigError as exc:
        # The loader's message contains server paths, so it is logged, never returned.
        logger.info("Config load failed: %s", exc)
        raise HTTPException(status_code=404, detail="unknown client") from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    # A sync endpoint: FastAPI runs it in a threadpool, so the blocking model call is fine.
    config = _load(request.client)
    system = build_system_prompt(config)
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    try:
        text = llm_reply(system, messages)
    except llm.LLMError as exc:
        logger.warning("Chat reply failed for client %s: %s", config.slug, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Sorry, the assistant is not available right now. "
                f"Please call us on {config.phone}."
            ),
        ) from exc
    return ChatResponse(reply=text)


@app.post("/lead", status_code=201)
def lead(request: LeadRequest) -> LeadResponse:
    config = _load(request.client)
    new_lead = leads.Lead(
        client=config.slug,
        name=request.name,
        phone=request.phone or "",
        email=request.email or "",
        message=request.message or "",
    )
    try:
        leads.save_lead(new_lead)
    except (OSError, ValueError) as exc:
        logger.error("Saving lead failed for client %s: %s", config.slug, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Sorry, we could not save your details. Please call us on {config.phone}.",
        ) from exc
    leads.notify_owner(new_lead, config.email)
    return LeadResponse(ok=True)
