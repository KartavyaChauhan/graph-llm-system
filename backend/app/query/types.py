"""Shared types for the structured query engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Stable intent identifiers (API / LLM tool contracts)
INTENT_FIND_TOP_PRODUCTS_BY_BILLING = "find_top_products_by_billing"
INTENT_TRACE_ORDER_FLOW = "trace_order_flow"
INTENT_FIND_INCOMPLETE_ORDERS = "find_incomplete_orders"
INTENT_TRACE_BILLING_FLOW = "trace_billing_flow"

SUPPORTED_INTENTS: tuple[str, ...] = (
    INTENT_FIND_TOP_PRODUCTS_BY_BILLING,
    INTENT_TRACE_ORDER_FLOW,
    INTENT_FIND_INCOMPLETE_ORDERS,
    INTENT_TRACE_BILLING_FLOW,
)


@dataclass(slots=True)
class ExecutionPlan:
    """
    Machine- and human-readable execution plan produced by the planner.

    ``steps`` is a list of opaque dicts describing operations; the executor knows how to interpret
    ``op`` keys. This keeps the planner auditable without encoding results.
    """

    intent: str
    parameters: dict[str, Any]
    human_readable: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueryError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
