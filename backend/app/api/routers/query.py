"""Natural language and structured query endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import EngineDep, TranslatorDep, llm_configured
from app.api.schemas import QueryRequest, QueryResponse, QueryTraceDTO, StructuredQueryBody
from app.api.services.answer import build_answer_from_pipeline
from app.llm.parser import run_natural_language_query

log = logging.getLogger("app.api.query")

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
def post_query(
    request: Request,
    body: QueryRequest,
    engine: EngineDep,
    translator: TranslatorDep,
) -> QueryResponse:
    """
    Pipeline: **user text → LLM intent parser → query engine → structured result.**

    Response shape: ``answer`` (deterministic summary), ``data`` (parse + engine payload), ``trace``.
    """
    rid = getattr(request.state, "request_id", None)
    if not llm_configured():
        raise HTTPException(
            status_code=503,
            detail="No LLM API key for the active LLM_PROVIDER. See backend/.env.example (GEMINI_API_KEY, or GROQ_*, or OPENROUTER_*).",
        )

    text = body.query.strip()
    try:
        pipeline = run_natural_language_query(
            engine,
            text,
            translator=translator,
            with_timings=True,
        )
    except Exception as exc:
        log.exception("query.pipeline_unhandled request_id=%s", rid)
        raise HTTPException(
            status_code=500,
            detail="Unexpected server error while running the query pipeline.",
        ) from exc

    answer = build_answer_from_pipeline(pipeline)
    timings = pipeline.get("timings_ms") or {}
    parse = pipeline.get("parse") or {}
    qr = pipeline.get("query_result")

    data: dict[str, Any] = {
        "user_query": text,
        "success": pipeline.get("success", False),
        "parse": parse,
        "query_result": qr,
        "result": (qr or {}).get("result") if isinstance(qr, dict) else None,
    }

    interpretation = (qr or {}).get("interpretation") if isinstance(qr, dict) else None
    err: dict[str, Any] | None = None
    if pipeline.get("error"):
        err = dict(pipeline["error"])
    elif isinstance(qr, dict) and not qr.get("success"):
        err = {"phase": "query", "detail": qr.get("error")}

    trace = QueryTraceDTO(
        request_id=str(rid) if rid else None,
        timings_ms={str(k): float(v) for k, v in timings.items()},
        parse=dict(parse),
        executed_query=bool(pipeline.get("executed_query")),
        intent=parse.get("intent"),
        parameters=dict(parse.get("parameters") or {}),
        interpretation=interpretation if isinstance(interpretation, dict) else None,
        engine_success=bool(pipeline.get("success")),
        error=err,
    )

    log.info(
        "query.done request_id=%s intent=%s success=%s",
        rid,
        parse.get("intent"),
        pipeline.get("success"),
    )
    return QueryResponse(answer=answer, data=data, trace=trace)


@router.post("/query/structured", response_model=dict[str, Any])
def post_query_structured(body: StructuredQueryBody, engine: EngineDep) -> dict[str, Any]:
    """Bypass LLM — run a known intent + parameters (tests & tooling)."""
    return engine.execute(body.intent, body.parameters)
