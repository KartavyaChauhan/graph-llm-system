"""
JSON-safe result shaping for APIs and LLM consumption.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.query.types import ExecutionPlan, QueryError


def json_safe(obj: Any) -> Any:
    """Recursively convert values to JSON-serializable forms."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return str(obj)


class ResultFormatter:
    def format_success(
        self,
        *,
        intent: str,
        parameters: dict[str, Any],
        plan: ExecutionPlan,
        raw: Any,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "intent": intent,
            "parameters": json_safe(parameters),
            "interpretation": {
                "human_readable": plan.human_readable,
                "data_sources": list(plan.data_sources),
                "plan_steps": json_safe(plan.steps),
            },
            "result": json_safe(raw),
            "llm_hints": {
                "summary_field": "result",
                "do_not_invent": "All figures come from result; cite material/order ids from rows only.",
            },
        }

    def format_error(
        self,
        *,
        intent: str,
        parameters: dict[str, Any],
        err: QueryError,
        plan: ExecutionPlan | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "success": False,
            "intent": intent,
            "parameters": json_safe(parameters),
            "error": {
                "code": err.code,
                "message": err.message,
                "details": json_safe(err.details),
            },
        }
        if plan is not None:
            body["interpretation"] = {
                "human_readable": plan.human_readable,
                "data_sources": list(plan.data_sources),
                "plan_steps": json_safe(plan.steps),
            }
        return body

    def dumps(self, payload: dict[str, Any], *, indent: int | None = 2) -> str:
        return json.dumps(payload, indent=indent, ensure_ascii=False)
