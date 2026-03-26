"""Structured query engine over bundle + graph."""

from typing import Any

from app.query.types import (
    INTENT_FIND_INCOMPLETE_ORDERS,
    INTENT_FIND_TOP_PRODUCTS_BY_BILLING,
    INTENT_TRACE_ORDER_FLOW,
    SUPPORTED_INTENTS,
    ExecutionPlan,
    QueryError,
)

__all__ = [
    "ExecutionPlan",
    "INTENT_FIND_INCOMPLETE_ORDERS",
    "INTENT_FIND_TOP_PRODUCTS_BY_BILLING",
    "INTENT_TRACE_ORDER_FLOW",
    "QueryEngine",
    "QueryError",
    "SUPPORTED_INTENTS",
]


def __getattr__(name: str) -> Any:
    if name == "QueryEngine":
        from app.query.engine import QueryEngine as qe

        return qe
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
