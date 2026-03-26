"""Meta routes: index, health, LLM configuration diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import TranslatorDep, llm_configured, llm_provider_name
from app.llm.parser import ALLOWED_GEMINI_MODELS, DEFAULT_MODEL_TRY_ORDER, GeminiIntentTranslator

router = APIRouter(tags=["meta"])


@router.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "Graph LLM System (O2C)",
        "version": "0.2.0",
        "docs": "/docs",
        "endpoints": {
            "health": "GET /health",
            "graph": "GET /graph",
            "query_nl": "POST /query",
            "query_structured": "POST /query/structured",
            "llm_models": "GET /llm/models",
        },
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/llm/models")
def llm_models(translator: TranslatorDep) -> dict[str, Any]:
    provider = llm_provider_name()
    body: dict[str, Any] = {
        "llm_provider": provider or "gemini",
        "configured_primary": translator.primary_model_name,
        "llm_configured": llm_configured(),
    }
    if isinstance(translator, GeminiIntentTranslator):
        body["gemini_allowed_models"] = sorted(ALLOWED_GEMINI_MODELS)
        body["gemini_default_try_order"] = list(DEFAULT_MODEL_TRY_ORDER)
    else:
        body["gemini_allowed_models"] = None
        body["gemini_default_try_order"] = None
        body["note"] = "Using OpenAI-compatible HTTP API; model id is taken from GROQ_MODEL or OPENROUTER_MODEL."
    return body
