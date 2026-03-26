"""
FastAPI application entrypoint.

Request flow (``POST /query``)
------------------------------
1. :class:`~app.api.middleware.RequestLoggingMiddleware` assigns ``X-Request-ID`` and logs latency.
2. :mod:`app.api.routers.query` receives the user string.
3. :func:`app.llm.parser.run_natural_language_query` calls the configured LLM translator, then
   :class:`app.query.engine.QueryEngine` on the in-memory bundle + graph.
4. :mod:`app.api.services.answer` builds a short **deterministic** ``answer`` from the pipeline output.
5. Response follows :class:`app.api.schemas.QueryResponse`: ``answer``, ``data``, ``trace``.

Component interaction
---------------------
* **Dependencies** (:mod:`app.api.deps`) — cached factory for :class:`~app.query.engine.QueryEngine`
  (loads JSONL once, builds :class:`~app.graph.manager.GraphManager`).
* **Routers** — ``meta`` (health, index), ``graph`` (export), ``query`` (NL + structured bypass).
* **Services** — graph serialization and answer synthesis contain no framework secrets.

Run from ``backend``::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.api.deps import get_query_engine
from app.api.middleware import RequestLoggingMiddleware
from app.api.router import api_router

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm caches so first request is not paying full load cost
    get_query_engine()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Graph LLM System (O2C)",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(api_router)
    # Dev UI (Vite on :5173) calls API on :8000. Set CORS_ORIGINS (comma-separated) in production.
    _raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    if _raw:
        cors_origins = [x.strip() for x in _raw.split(",") if x.strip()]
    else:
        cors_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()
