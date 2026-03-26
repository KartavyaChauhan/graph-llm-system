"""
Query planner: maps intent + parameters to an :class:`ExecutionPlan`.

No data access here — only validation and plan construction so interpretation is loggable and
testable in isolation.
"""

from __future__ import annotations

from typing import Any

from app.query.types import (
    INTENT_FIND_INCOMPLETE_ORDERS,
    INTENT_FIND_TOP_PRODUCTS_BY_BILLING,
    INTENT_TRACE_BILLING_FLOW,
    INTENT_TRACE_ORDER_FLOW,
    SUPPORTED_INTENTS,
    ExecutionPlan,
    QueryError,
)


class QueryPlanner:
    def plan(self, intent: str, parameters: dict[str, Any]) -> ExecutionPlan | QueryError:
        intent = (intent or "").strip()
        if intent not in SUPPORTED_INTENTS:
            return QueryError(
                code="unknown_intent",
                message=f"Unknown intent {intent!r}.",
                details={"supported_intents": list(SUPPORTED_INTENTS)},
            )

        if intent == INTENT_FIND_TOP_PRODUCTS_BY_BILLING:
            return self._plan_top_products(dict(parameters or {}))
        if intent == INTENT_TRACE_ORDER_FLOW:
            return self._plan_trace_order(dict(parameters or {}))
        if intent == INTENT_FIND_INCOMPLETE_ORDERS:
            return self._plan_incomplete_orders(dict(parameters or {}))
        if intent == INTENT_TRACE_BILLING_FLOW:
            return self._plan_trace_billing(dict(parameters or {}))

        return QueryError(code="internal", message="Planner routing bug.", details={"intent": intent})

    def _plan_top_products(self, p: dict[str, Any]) -> ExecutionPlan:
        limit = int(p.get("limit", 10))
        limit = max(1, min(limit, 500))
        sort_by = str(p.get("sort_by", "billing_line_count")).strip() or "billing_line_count"
        if sort_by not in ("billing_line_count", "net_amount_sum"):
            sort_by = "billing_line_count"
        language = str(p.get("language", "EN")).strip() or "EN"
        steps = [
            {
                "op": "aggregate_billing_items_by_material",
                "limit": limit,
                "sort_by": sort_by,
                "language": language,
            }
        ]
        hr = (
            f"Aggregate all billing document line items from the data bundle by material; "
            f"compute per-material billing_line_count and net_amount_sum; "
            f"sort by {sort_by} descending; take top {limit}. "
            f"Enrich with product description for language {language!r} when available."
        )
        return ExecutionPlan(
            intent=INTENT_FIND_TOP_PRODUCTS_BY_BILLING,
            parameters={"limit": limit, "sort_by": sort_by, "language": language},
            human_readable=hr,
            steps=steps,
            data_sources=["O2CDataBundle.billing_items", "O2CDataBundle.product_descriptions (optional)"],
        )

    def _plan_trace_order(self, p: dict[str, Any]) -> ExecutionPlan | QueryError:
        oid = p.get("order_id") or p.get("sales_order")
        if oid is None or str(oid).strip() == "":
            return QueryError(
                code="missing_parameter",
                message="trace_order_flow requires 'order_id' or 'sales_order'.",
                details={},
            )
        order_key = str(oid).strip()
        include_metadata = bool(p.get("include_node_metadata", True))
        max_paths = int(p.get("max_paths", 256))
        max_paths = max(1, min(max_paths, 2000))
        steps = [
            {
                "op": "multi_hop_order_to_cash_paths",
                "order_key": order_key,
                "include_node_metadata": include_metadata,
                "max_paths": max_paths,
            },
            {"op": "order_lifecycle_snapshot", "order_key": order_key},
        ]
        hr = (
            f"Resolve order {order_key!r} to a graph node; "
            f"enumerate acyclic O2C paths Order->Delivery->Invoice->Payment (up to {max_paths} paths); "
            f"attach {'full' if include_metadata else 'minimal'} node metadata from the graph store; "
            f"include order_lifecycle summary for missing-link diagnostics."
        )
        return ExecutionPlan(
            intent=INTENT_TRACE_ORDER_FLOW,
            parameters={
                "order_id": order_key,
                "include_node_metadata": include_metadata,
                "max_paths": max_paths,
            },
            human_readable=hr,
            steps=steps,
            data_sources=["GraphManager (O2C typed edges)", "GraphManager.order_lifecycle"],
        )

    def _plan_incomplete_orders(self, p: dict[str, Any]) -> ExecutionPlan:
        raw = p.get("criteria")
        if raw is None:
            criteria = [
                "missing_customer",
                "missing_delivery",
                "missing_invoice",
                "missing_payment",
                "missing_product_lines",
            ]
        elif isinstance(raw, (list, tuple)):
            criteria = [str(x).strip() for x in raw if str(x).strip()]
        else:
            criteria = [str(raw).strip()]

        allowed = {
            "missing_customer",
            "missing_delivery",
            "missing_invoice",
            "missing_payment",
            "missing_product_lines",
        }
        criteria = [c for c in criteria if c in allowed]
        if not criteria:
            criteria = list(allowed)

        limit = int(p.get("limit", 200))
        limit = max(1, min(limit, 5000))
        steps = [
            {
                "op": "filter_orders_by_lifecycle_gaps",
                "criteria": criteria,
                "limit": limit,
            }
        ]
        hr = (
            f"Iterate all Order nodes in the graph; for each, compute lifecycle via typed edges; "
            f"keep orders matching any of {criteria!r} (conditional OR); return at most {limit} rows."
        )
        return ExecutionPlan(
            intent=INTENT_FIND_INCOMPLETE_ORDERS,
            parameters={"criteria": criteria, "limit": limit},
            human_readable=hr,
            steps=steps,
            data_sources=["GraphManager.iter_nodes_by_type(Order)", "GraphManager.order_lifecycle"],
        )

    def _plan_trace_billing(self, p: dict[str, Any]) -> ExecutionPlan | QueryError:
        bid = p.get("billing_document") or p.get("invoice_id") or p.get("billing_id")
        if bid is None or str(bid).strip() == "":
            return QueryError(
                code="missing_parameter",
                message="trace_billing_flow requires 'billing_document' (invoice/billing number).",
                details={},
            )
        billing_document = str(bid).strip()
        include_metadata = bool(p.get("include_node_metadata", True))
        max_paths = int(p.get("max_paths", 256))
        max_paths = max(1, min(max_paths, 2000))
        steps = [
            {
                "op": "trace_billing_document_flow",
                "billing_document": billing_document,
                "include_node_metadata": include_metadata,
                "max_paths": max_paths,
            }
        ]
        hr = (
            f"Resolve billing document {billing_document!r} to an Invoice node; "
            f"walk upstream to Delivery and Order, and downstream to Journal Entry and Payment when present; "
            f"enumerate up to {max_paths} path variants; "
            f"attach {'full' if include_metadata else 'minimal'} node metadata."
        )
        return ExecutionPlan(
            intent=INTENT_TRACE_BILLING_FLOW,
            parameters={
                "billing_document": billing_document,
                "include_node_metadata": include_metadata,
                "max_paths": max_paths,
            },
            human_readable=hr,
            steps=steps,
            data_sources=["GraphManager (typed edges)"],
        )
