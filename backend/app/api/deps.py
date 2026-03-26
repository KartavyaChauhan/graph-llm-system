"""
FastAPI dependencies: shared application state (engine, translator, graph).

Inject these via ``Depends`` so routers stay thin and testable.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.db.loader import load_o2c_bundle
from app.graph.graph_builder import build_graph_from_bundle
from app.graph.manager import GraphManager
from app.llm.parser import GeminiIntentTranslator, IntentTranslatorBase
from app.query.engine import QueryEngine

logger = logging.getLogger("app.api.deps")


def llm_provider_name() -> str:
    """Active provider: ``gemini``, ``groq``, or ``openrouter`` (default ``gemini``)."""
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


@lru_cache(maxsize=1)
def get_query_engine() -> QueryEngine:
    bundle = load_o2c_bundle()
    graph, _ = build_graph_from_bundle(bundle)
    return QueryEngine(bundle, graph)


@lru_cache(maxsize=1)
def get_graph_manager() -> GraphManager:
    """Same graph projection as bound to :func:`get_query_engine` (in-memory rebuild)."""
    return get_query_engine().graph


def llm_configured() -> bool:
    """True when the active :envvar:`LLM_PROVIDER` has its API key set."""
    p = llm_provider_name()
    if p == "groq":
        return bool(os.environ.get("GROQ_API_KEY", "").strip())
    if p == "openrouter":
        return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if p not in ("gemini", ""):
        logger.warning("llm.unknown_provider treating_as_gemini value=%r", p)
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


@lru_cache(maxsize=1)
def get_translator() -> IntentTranslatorBase:
    p = llm_provider_name()
    if p == "groq":
        from app.llm.openai_provider import OpenAICompatibleTranslator

        return OpenAICompatibleTranslator(provider="groq")
    if p == "openrouter":
        from app.llm.openai_provider import OpenAICompatibleTranslator

        return OpenAICompatibleTranslator(provider="openrouter")
    if p not in ("gemini", ""):
        logger.warning("llm.unknown_provider falling_back_to_gemini value=%r", p)
    return GeminiIntentTranslator()


EngineDep = Annotated[QueryEngine, Depends(get_query_engine)]
GraphDep = Annotated[GraphManager, Depends(get_graph_manager)]
TranslatorDep = Annotated[IntentTranslatorBase, Depends(get_translator)]
