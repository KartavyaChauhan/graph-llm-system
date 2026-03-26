"""Graph export for visualization clients."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import GraphDep
from app.api.schemas import GraphResponse
from app.api.services.graph_export import build_graph_response

log = logging.getLogger("app.api.graph")

router = APIRouter(tags=["graph"])


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    request: Request,
    manager: GraphDep,
    max_nodes: int = Query(default=8000, ge=100, le=100_000),
    max_edges: int = Query(default=100_000, ge=100, le=500_000),
    metadata_max_chars: int = Query(default=400, ge=0, le=10_000),
) -> GraphResponse:
    """
    Return nodes and typed edges for the in-memory O2C graph.

    Large graphs may be truncated; check ``truncated`` and ``warnings`` in the response.
    """
    rid = getattr(request.state, "request_id", None)
    try:
        out = build_graph_response(
            manager,
            max_nodes=max_nodes,
            max_edges=max_edges,
            metadata_max_chars=metadata_max_chars,
        )
        log.info(
            "graph.export request_id=%s nodes=%s edges=%s truncated=%s",
            rid,
            len(out.nodes),
            len(out.edges),
            out.truncated,
        )
        return out
    except Exception:
        log.exception("graph.export_failed request_id=%s", rid)
        raise HTTPException(status_code=500, detail="Failed to serialize graph for API export.") from None
