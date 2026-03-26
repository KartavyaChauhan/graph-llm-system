"""API request/response contracts."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class GraphQueryParams(BaseModel):
    """Optional limits for large graphs (visualization payloads)."""

    max_nodes: int = Field(default=8000, ge=100, le=100_000)
    max_edges: int = Field(default=100_000, ge=100, le=500_000)
    metadata_max_chars: int = Field(default=400, ge=0, le=10_000)


class GraphNodeDTO(BaseModel):
    id: str
    type: str
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeDTO(BaseModel):
    source: str
    target: str
    type: str
    key: int | str
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    nodes: list[GraphNodeDTO]
    edges: list[GraphEdgeDTO]
    stats: dict[str, Any]
    truncated: bool
    warnings: list[str] = Field(default_factory=list)


class QueryRequest(BaseModel):
    """Natural-language query body for ``POST /query``."""

    query: str = Field(..., min_length=1, max_length=6000)


class StructuredQueryBody(BaseModel):
    """Structured query body for ``POST /query/structured`` (no LLM)."""

    intent: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class QueryTraceDTO(BaseModel):
    request_id: Optional[str] = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
    parse: dict[str, Any] = Field(default_factory=dict)
    executed_query: bool = False
    intent: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    interpretation: Optional[dict[str, Any]] = None
    engine_success: bool = False
    error: Optional[dict[str, Any]] = None


class QueryResponse(BaseModel):
    """
    Unified response for ``POST /query``.

    * ``answer`` — short, **data-backed** summary (deterministic; not a second generative LLM).
    * ``data`` — primary payload from the query engine (and parse snapshot when useful).
    * ``trace`` — audit / debugging: timings, intent, interpretation, errors.
    """

    answer: str
    data: dict[str, Any]
    trace: QueryTraceDTO
